import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.config import Config, config
from ethernity_cloud_sdk_py.commands.spinner import Spinner

config = Config(Path(".config.json").resolve())
config.load()

# For accessing package resources
try:
    from importlib.resources import path as resources_path
except ImportError:
    # For Python versions < 3.7
    from importlib_resources import path as resources_path  # type: ignore

def run_command(command, redirect_output=False):
    """
    Execute a shell command without producing output on the terminal.
    """

    stdout = subprocess.DEVNULL if redirect_output else None  # Redirect standard output to devnull
    stderr = subprocess.DEVNULL if redirect_output else None

    result = subprocess.run(command, stdout=stdout, stderr=stderr, text=True, shell=True)

    if result.returncode != 0:
        # Handle non-zero exit code
        raise RuntimeError(f"\n\nCommand '{command}' failed with exit code {result.returncode}")

    return result


def get_command_output(command):
    """
    Execute a shell command and return its output.
    """
    result = subprocess.run(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8").strip()


def get_docker_server_info():
    try:
        # Ask the daemon directly for its server version. This is the robust way
        # to tell "Docker is running": it returns a non-empty version and exit 0
        # only when the daemon is reachable. The previous approach parsed the
        # human-readable `docker info` output and broke at the first empty line
        # in the Server section, which produced false negatives on Docker
        # versions whose output layout differed.
        result = subprocess.check_output(
            "docker info --format {{.ServerVersion}}",
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return bool(result.strip())
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False

def clean_up_registry():
    # Remove the 'registry' directory if it exists
    shutil.rmtree("./build/registry", ignore_errors=True)

    # Stop and remove any running Docker containers or images that might conflict
    dockerPS = get_command_output("docker ps --filter name=registry -a -q")
    if dockerPS:
        run_command(f'docker stop {dockerPS}', True)
        run_command(f"docker rm {dockerPS} -f", True)

    remainingContainers = get_command_output("docker ps --filter 'name=*etny*' -a -q")
    if remainingContainers:
        run_command(f"docker stop {remainingContainers}", True)
        run_command(f"docker rm {remainingContainers} -f", True)

    remainingContainers = get_command_output("docker ps --filter 'name=las' -a -q")
    if remainingContainers:
        run_command(f"docker stop {remainingContainers}", True)
        run_command(f"docker rm {remainingContainers} -f", True)

    dockerImgReg = get_command_output(
        'docker images --filter reference="*registry*" -q'
    )

    if dockerImgReg:
        run_command(f'docker rmi {" ".join(dockerImgReg.splitlines())} -f', True)

    dockerImgReg = get_command_output(
        'docker images --filter reference="*etny*" -q'
    )
    if dockerImgReg:
        run_command(f'docker rmi {" ".join(dockerImgReg.splitlines())} -f', True)

    return True

def copy_backend_to_build_dir(build_dir):
    # Copy serverless source code (including subdirectories) to the build directory

    src_dir = Path.cwd() / "src" / "serverless"
    dest_dir = Path(build_dir) / "securelock" / "src" / "serverless"

    # Fail the build NOW if the backend is missing or unparseable. An enclave
    # built without a valid backend runs every task into
    # "name 'X' is not defined" / IMPORT_ERROR on-chain — expensive to
    # discover after building, publishing and paying for a task.
    backend_file = src_dir / "backend.py"
    if not backend_file.is_file():
        print(f"ERROR: {backend_file} not found.")
        print("       The securelock enclave loads your functions from src/serverless/backend.py;")
        print("       without it no backend function will exist inside the enclave.")
        print("       Run ecld-init to scaffold it, or create the file before building.")
        sys.exit(1)
    backend_source = backend_file.read_text(encoding="utf-8")
    try:
        import ast as _ast

        _ast.parse(backend_source)
    except SyntaxError as e:
        print(f"ERROR: src/serverless/backend.py has a syntax error and cannot be imported")
        print(f"       inside the enclave: line {e.lineno}: {e.msg}")
        sys.exit(1)

    # Safety lint: flag dynamic code execution -- especially of task input --
    # before the image is sealed. A hard error only for the case that actually
    # opens the enclave to a submitter (eval/exec/compile of ___etny_data_set___);
    # everything else is a warning. See payload_lint.py for scope + opt-out.
    try:
        from ethernity_cloud_sdk_py.commands.pynithy.payload_lint import analyze as _lint
    except Exception:
        _lint = None
    if _lint is not None:
        findings, opted_out = _lint(backend_source, filename="src/serverless/backend.py")
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        for f in warnings:
            print(f"WARNING: src/serverless/backend.py:{f.line}: {f.message}")
        if errors:
            print("")
            print("ERROR: unsafe dynamic execution of task input in "
                  "src/serverless/backend.py:")
            for f in errors:
                print(f"       line {f.line}: {f.message}")
            print("")
            print("       This would let a task submitter run arbitrary code inside "
                  "your enclave")
            print("       and reach other users' state. Fix it, or if the input is "
                  "genuinely trusted,")
            print("       add `# ecld: allow-eval` on that line to acknowledge the "
                  "risk.")
            sys.exit(1)

    # Remove destination directory if it exists to avoid conflicts
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    # Copy entire directory tree
    shutil.copytree(src_dir, dest_dir)

    return True


def copy_from_module_to_build_dir(build_dir):
    # Copy module files from module dir to build dir
    module_dir = Path(__file__).resolve().parent

    build_dir.mkdir(parents=True, exist_ok=True)

    scripts_dir = build_dir / "securelock" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)


    src_file = module_dir / "build" / "securelock" / "Dockerfile.base.tpl"
    dest_file = build_dir / "securelock" / "Dockerfile.base.tpl"
    shutil.copy(src_file, dest_file)


    src_file = module_dir / "build" / "securelock" / "Dockerfile.tpl"
    dest_file = build_dir / "securelock" / "Dockerfile.tpl"
    shutil.copy(src_file, dest_file)

    src_file = module_dir / "build" / "securelock" / "scripts" / "binary-fs-build.sh"
    dest_file = build_dir / "securelock" / "scripts" / "binary-fs-build.sh"
    shutil.copy(src_file, dest_file)

    # pyfreeze.sh runs PyInstaller off-SCONE in the pyfreeze build stage; it must
    # be copied into the build dir alongside binary-fs-build.sh or the build
    # fails ("pyfreeze.sh: not found").
    src_file = module_dir / "build" / "securelock" / "scripts" / "pyfreeze.sh"
    dest_file = build_dir / "securelock" / "scripts" / "pyfreeze.sh"
    shutil.copy(src_file, dest_file)

    src_file = module_dir / "build" / "securelock" / "src"
    dest_file = build_dir / "securelock" / "src"
    # Remove dest if it exists (since copytree fails if dest exists)

    if dest_file.exists():
        shutil.rmtree(dest_file)
    # The SGX key-gen module ships as a PREBUILT get_sgx_report.so (never the
    # .c source). Guard the build with a clear error if the .so is missing --
    # e.g. a checkout that never ran scripts/build_keygen_so.sh.
    so_path = src_file / "get_sgx_report.so"
    if not so_path.is_file():
        print(f"ERROR: {so_path} not found.")
        print("       The SDK ships the SGX key-gen module as a prebuilt .so, not source.")
        print("       It is a build artifact of the etny-pynithy / etny-nodenithy")
        print("       pipelines. Copy it in with scripts/get_keygen_so.sh and commit")
        print("       src/get_sgx_report.so (see scripts/README-keygen.md).")
        sys.exit(1)
    shutil.copytree(src_file, dest_file)

    return True

def update_dockerfile():
    PROJECT_NAME = config.read("PROJECT_NAME")
    BLOCKCHAIN_NETWORK = config.read("BLOCKCHAIN_NETWORK")
    VERSION = config.read("VERSION")
    TRUSTED_ZONE_IMAGE = config.read("TRUSTED_ZONE_IMAGE")
    DOCKER_REPO_URL = config.read("DOCKER_REPO_URL")
    BASE_IMAGE_TAG = config.read("BASE_IMAGE_TAG")

    # Generate the enclave name for securelock
    SECURELOCK_SESSION = f"{PROJECT_NAME}-SECURELOCK-V3-{BLOCKCHAIN_NETWORK.split('_')[1].lower()}-{VERSION}".replace(
        "/", "_"
    ).replace(
        "-", "_"
    )

    config.write("SECURELOCK_SESSION", SECURELOCK_SESSION)

    os.chdir("securelock")
   
    # Modify Dockerfile based on the template
    with open("Dockerfile.base.tpl", "r") as f:
        dockerfile_secure_template = f.read()

    dockerfile_secure_content = (
        dockerfile_secure_template.replace("__DOCKER_REPO_URL__", DOCKER_REPO_URL)
        .replace("__BASE_IMAGE_TAG__", BASE_IMAGE_TAG)
    )

    with open("Dockerfile.base", "w") as f:
        f.write(dockerfile_secure_content)

    return True


def start_local_registry():
    # Set up Docker registry
    run_command("docker pull registry:2", True)
    run_command("docker run -d --restart=always -p 5000:5000 --name registry registry:2", True)

    return True


def build_and_push_services(build_dir: str):
    """
    Scan build_dir/svc, build each service via its Dockerfile,
    and push to the local registry at localhost:5000.
    """
    svc_root = os.path.join(build_dir, 'securelock', 'src', 'serverless', 'svc')
    if not os.path.isdir(svc_root):
        print(f"No svc directory found at {svc_root!r}")
        return True

    for svc_name in os.listdir(svc_root):
        svc_path = os.path.join(svc_root, svc_name)
        if not os.path.isdir(svc_path):
            continue

        image_tag = f"localhost:5000/{svc_name}:latest"

        # Build the Docker image
        subprocess.run(
            ["docker", "build", "-t", image_tag, svc_path],
            check=True
        )

        # Push to local registry
        subprocess.run(
            ["docker", "push", image_tag],
            check=True
        )

    return True

def validate_esr_config():
    """ESR fail-fast gate (RFC §5.4): never build an ESR-enabled enclave with
    an unresolved registry address — inside the sealed image it would read as
    empty and every task would fail after gas is spent.

    Address resolution, in order:
      1. an explicitly configured contract_address (BYO / private registry), else
      2. the canonical deployment for this network, shipped with the SDK.

    Shipping the canonical address is the point: hand-wiring it is exactly the
    gap that produced the empty-result bug. A network with no deployment fails
    the build here rather than sealing an empty value into the image.
    """
    esr = config.read_esr()
    if not esr.get("enabled"):
        return esr

    network = config.read("BLOCKCHAIN_NETWORK")
    addr = (esr.get("contract_address") or "").strip()
    source = "configured"
    if not addr:
        addr = (BlockchainNetworks.get_esr_contract_address(network) or "").strip()
        source = "canonical (shipped with the SDK)"

    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
        if not addr:
            print(f"ERROR: ESR is enabled but no ESR registry is deployed on {network}.")
            print("       The enclave is sealed: an empty address bakes in and every task")
            print("       fails at runtime after gas is spent. Either build for a network")
            print("       that has one, or set an explicit address (ECLD_ESR_CONTRACT /")
            print("       .config.json ESR.contract_address) pointing at your own registry.")
        else:
            print("ERROR: ESR is enabled but the ESR contract_address is not a valid"
                  f" address (got {addr!r}).")
            print("       Set it with ecld-init (ESR step), ECLD_ESR_CONTRACT, or")
            print("       .config.json ESR.contract_address")
        sys.exit(1)

    # Persist the resolved address so publish/runtime see the same value the
    # image was built with, and the developer can see what was baked in.
    if esr.get("contract_address") != addr:
        esr["contract_address"] = addr
        config.write_esr(esr)
    print(f"\t✔  ESR registry [{source}]: {addr}")
    return esr


def main():
    global current_dir
    # Set current directory
    current_dir = os.getcwd()
    # Set the build directory path
    build_dir = Path.cwd() / "build"

    ESR = validate_esr_config()

    copy_from_module_to_build_dir(build_dir)

    spinner = Spinner()

    BLOCKCHAIN_NETWORK = config.read("BLOCKCHAIN_NETWORK")
    DAPP_TYPE = config.read("DAPP_TYPE")

    BLOCKCHAIN_CONFIG = BlockchainNetworks.get_details_by_enum_name(BLOCKCHAIN_NETWORK)

    TEMPLATE_CONFIG = BLOCKCHAIN_CONFIG.template_image.get(DAPP_TYPE)
    
    TRUSTED_ZONE_IMAGE = TEMPLATE_CONFIG.trusted_zone_image
    DOCKER_REPO_URL = TEMPLATE_CONFIG.docker_repo_url
    BASE_IMAGE_TAG = TEMPLATE_CONFIG.base_image_tag
    DOCKER_LOGIN = TEMPLATE_CONFIG.docker_login
    DOCKER_PASSWORD = TEMPLATE_CONFIG.docker_password

    config.write("TRUSTED_ZONE_IMAGE", TRUSTED_ZONE_IMAGE)
    config.write("BASE_IMAGE_TAG",BASE_IMAGE_TAG)
    config.write("DOCKER_REPO_URL", DOCKER_REPO_URL)
    config.write("DOCKER_LOGIN", DOCKER_LOGIN)
    config.write("DOCKER_PASSWORD", DOCKER_PASSWORD)



    # In non-interactive mode (ECLD_NON_INTERACTIVE / ECLD_ASSUME_YES, or no TTY
    # such as CI/CD) don't block on input(); use ECLD_MEMORY_TO_ALLOCATE if set,
    # otherwise the 1GB default.
    def _memory_non_interactive():
        if os.environ.get("ECLD_NON_INTERACTIVE", "").strip().lower() in ("1", "true", "yes"):
            return True
        if os.environ.get("ECLD_ASSUME_YES", "").strip().lower() in ("1", "true", "yes"):
            return True
        try:
            return not sys.stdin.isatty()
        except Exception:
            return False

    while config.read("MEMORY_TO_ALLOCATE") is None:
        if _memory_non_interactive():
            memory_input = os.environ.get("ECLD_MEMORY_TO_ALLOCATE", "1GB").strip()
            print(f"\n\tMemory to allocate [non-interactive -> {memory_input}]")
        else:
            memory_input = input("\n\tEnter memory to allocate (e.g., '2GB', '512M', '4 G', etc.) [1GB]: ").strip()

        if memory_input == "":
            memory_input = "1GB"

        # Regex pattern to extract the integer and unit (GB or MB)
        match = re.match(r'^(\d+)\s*(gb|g|mb|m)?$', memory_input, re.IGNORECASE)

        if match:
            value = int(match.group(1))
            unit = match.group(2)

            if unit is None:
                # Default to GB if no unit provided
                unit = 'GB'
            else:
                unit = unit.upper()


            if unit in ('GB', 'G'):
                if 1 <= value < 128:
                    final_value = f"{value}G"
                    config.write("MEMORY_TO_ALLOCATE", final_value)
                    break
                else:
                    print("Please enter a valid memory allocation between 1 and 128GB.")
            elif unit in ('MB', 'M'):
                if 128 <= value < 131072:  # Between 128 MB and 128 GB
                    if value % 1024 == 0:
                        final_value = f"{value // 1024}G"
                    else:
                        final_value = f"{value}M"
                    config.write("MEMORY_TO_ALLOCATE", final_value)
                    break
                else:
                    print("Please enter a valid memory allocation between 128MB and 131072MB (128GB).")
            else:
                print("Invalid unit. Please enter memory in GB or MB.")
        else:
            print("Invalid format. Please enter a number followed by 'GB', 'MB', 'G', or 'M' (e.g., '16GB', '512M').")

    dockerPS = spinner.spin_till_done("Checking docker service", get_docker_server_info)

    if dockerPS == False:
        print("""
\t\tDocker service is not running. Please start docker to continue.
\t\tMore information about installing and running Docker can be founde here: https://docs.docker.com/engine/install/
""")
        exit(1)
  
    MEMORY_TO_ALLOCATE = config.read("MEMORY_TO_ALLOCATE")

    spinner.spin_till_done(f"Binary will use {MEMORY_TO_ALLOCATE} memory", get_docker_server_info)

    spinner.spin_till_done("Cleanup local registry", clean_up_registry)

    spinner.spin_till_done("Copy backend files from src to build directory", copy_backend_to_build_dir, build_dir)

    spinner.spin_till_done("Start local registry", start_local_registry)

    
    # Change directory to the build directory
    os.chdir(build_dir)

    spinner.spin_till_done("Update dockerfile ", update_dockerfile)

    SECURELOCK_SESSION = config.read("SECURELOCK_SESSION")

    # Build and push Docker image for etny-securelock-base

    print()
    print(f"\u276f\u276f Building base image")
    print()

    run_command("docker build -f Dockerfile.base -t etny-securelock-base:latest .")

    # Adding dockerfile customizations

    if os.path.exists("src/serverless/Dockerfile.serverless"):
        print()
        print(f"\u276f\u276f Adding customizations from Dockerfile.serverless")
        print()
        run_command("docker build -f src/serverless/Dockerfile.serverless -t etny-securelock-serverless:latest .")
    else:
        run_command("docker tag localhost:5000/etny-securelock-base:latest etny-securelock-serverless:latest")

    print()
    print(f"\u276f\u276f Building securelock image")
    print()

    with open("Dockerfile.tpl", "r") as f:
        dockerfile_secure_template = f.read()

    MEMORY_TO_ALLOCATE_FORMATED = MEMORY_TO_ALLOCATE

    dockerfile_secure_content = (
        dockerfile_secure_template.replace(
            "__SECURELOCK_SESSION__", SECURELOCK_SESSION
        )
        .replace("__BUCKET_NAME__", TRUSTED_ZONE_IMAGE + "-v3")
        .replace(
            "__SMART_CONTRACT_ADDRESS__",
            BLOCKCHAIN_CONFIG.protocol_contract_address,
        )
        .replace("__IMAGE_REGISTRY_ADDRESS__", BLOCKCHAIN_CONFIG.image_registry_contract_address)
        .replace("__RPC_URL__", BLOCKCHAIN_CONFIG.rpc_url)
        .replace("__CHAIN_ID__", str(BLOCKCHAIN_CONFIG.chain_id))
        .replace("__TRUSTED_ZONE_IMAGE__", TRUSTED_ZONE_IMAGE)
        .replace("__NETWORK_TYPE__", BLOCKCHAIN_CONFIG.network_type)
        # ethernity-cas ValidatorRegistry, baked so the enclave can verify the
        # CAS that provisions it (ECAS_CAS_QUOTE). "" = no registry on this
        # network -> the enclave skips the check.
        .replace("__VALIDATOR_REGISTRY_ADDRESS__",
                 BlockchainNetworks.get_validator_registry_address(BLOCKCHAIN_NETWORK) or "")
        .replace("__MEMORY_TO_ALLOCATE__", MEMORY_TO_ALLOCATE_FORMATED)
    )

    # ESR env injection (RFC §5.4). Disabled projects get the placeholder line
    # removed entirely, so the rendered Dockerfile is byte-identical to a
    # pre-ESR render and existing MRENCLAVEs are unaffected. Enabled projects
    # get the address(es) baked as runtime env in the final (signed) stage.
    esr_env_block = ""
    if ESR.get("enabled"):
        esr_env_block = f"ENV ESR_CONTRACT_ADDRESS={ESR['contract_address']}\n"
        if (ESR.get("wallet_address") or "").strip():
            esr_env_block += f"ENV ESR_WALLET_ADDRESS={ESR['wallet_address']}\n"
    dockerfile_secure_content = dockerfile_secure_content.replace(
        "__ESR_ENV__\n", esr_env_block
    )

    # CRITICAL: sign /usr/local/bin/python -- the binary the enclave actually
    # EXECUTES (ENTRYPOINT and the publish/run compose command both run
    # /usr/local/bin/python). /usr/local/bin/python3 is a SEPARATE symlink ->
    # python3.14; signing that leaves the executed binary as the base image's
    # DEBUG-signed one (Debug: yes, heap 64MB), so at runtime SCONE recomputes
    # MRENCLAVE and dynamically re-signs as debug -> CAS rejects the DCAP quote
    # ("Debug mode is enabled -> enclave not trustworthy") on mainnet.
    #
    # The enclave-creation params (--heap/--stack/--dlopen/--extensions) MUST be
    # passed explicitly and match the runtime env exactly (scone-signer embeds
    # SCONE defaults for anything not passed as a flag) -- any drift triggers the
    # same debug re-sign. Values mirror publish.py's securelock_env.
    sign_flags = (
        f"--key=/enclave-key.pem --env --heap={MEMORY_TO_ALLOCATE_FORMATED} "
        f"--stack=4M --dlopen=1 --extensions=/lib/libbinary-fs.so"
    )
    if BLOCKCHAIN_CONFIG.network_type == 'mainnet':
        dockerfile_secure_content_final_signed = dockerfile_secure_content.replace(
            "__SCONE_SIGN__", f"RUN scone-signer sign {sign_flags} --production /usr/local/bin/python"
        ).replace( "__SCONE_ALLOW_DLOPEN__", "ENV SCONE_ALLOW_DLOPEN=1")

    if BLOCKCHAIN_CONFIG.network_type == 'testnet':
        dockerfile_secure_content_final_signed = dockerfile_secure_content.replace(
            "__SCONE_SIGN__", f"RUN scone-signer sign {sign_flags} /usr/local/bin/python"
        ).replace( "__SCONE_ALLOW_DLOPEN__", "ENV SCONE_ALLOW_DLOPEN=1")


    with open("Dockerfile", "w") as f:
        f.write(dockerfile_secure_content_final_signed)


    # Adding dockerfile customizations

    # Build and push Docker image for etny-securelock
    
    run_command(
        f"docker build --build-arg SECURELOCK_SESSION={SECURELOCK_SESSION} -t etny-securelock:latest ."
    )
    run_command("docker tag etny-securelock localhost:5000/etny-securelock")

    print()
    print(f"\u276f\u276f Pushing securelock image to local registry")
    print()

    run_command("docker push localhost:5000/etny-securelock")

    # Return to the build directory
    os.chdir("..")

    # Build etny-trustedzone
    print()
    print(f"\u276f\u276f Building trustedzone image")
    print()

    run_command(
        f"docker pull registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/{TRUSTED_ZONE_IMAGE}/trustedzone:{BLOCKCHAIN_NETWORK.lower()}"
    )
    run_command(
        f"docker tag registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/{TRUSTED_ZONE_IMAGE}/trustedzone:{BLOCKCHAIN_NETWORK.lower()} localhost:5000/etny-trustedzone"
    )

    print()
    print(f"\u276f\u276f Pushing trustedzone image to local registry")
    print()

    run_command("docker push localhost:5000/etny-trustedzone")

    # # Build etny-validator
    # print("Building validator")
    # os.chdir("../validator")
    # run_command("docker build -t etny-validator:latest .")
    # run_command("docker tag etny-validator localhost:5000/etny-validator")
    # run_command("docker push localhost:5000/etny-validator")

    # Build etny-las
    print()
    print(f"\u276f\u276f Building las image")
    print()

    run_command(
        "docker pull registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/las:scone6.0.7"
    )
    run_command(
        "docker tag registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/las:scone6.0.7 localhost:5000/etny-las"
    )

    print()
    print(f"\u276f\u276f Pushing las image to local registry")
    print()

    run_command("docker push localhost:5000/etny-las")


    print()
    print(f"\u276f\u276f Building svc image(s)")
    print()

    build_and_push_services(build_dir)

    print()
    print(f"\u276f\u276f Cleaning up")
    print()
    # Return to the original directory
    os.chdir(current_dir)
    run_command("docker cp registry:/var/lib/registry ./build/registry")

    dest_dir = os.path.join(build_dir, "securelock", "src", "serverless")
    shutil.rmtree(dest_dir, ignore_errors=True)
