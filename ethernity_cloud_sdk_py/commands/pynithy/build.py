import os
import sys
import shutil
import subprocess
from pathlib import Path
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


ECRunner = {
    "etny-pynithy-testnet": [
        "0x02882F03097fE8cD31afbdFbB5D72a498B41112c",
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31",
        "https://core.bloxberg.org",
        8995,
    ],
    "etny-nodenithy-testnet": [
        "0x02882F03097fE8cD31afbdFbB5D72a498B41112c",
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31",
        "https://core.bloxberg.org",
        8995,
    ],
    "etny-pynithy": [
        "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7",
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31",
        "https://core.bloxberg.org",
        8995,
    ],
    "etny-nodenithy": [
        "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7",
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31",
        "https://core.bloxberg.org",
        8995,
    ],
    "ecld-nodenithy-testnet": [
        "0x4274b1188ABCfa0d864aFdeD86bF9545B020dCDf",
        "0xF7F4eEb3d9a64387F4AcEb6d521b948E6E2fB049",
        "https://rpc-mumbai.matic.today",
        80001,
    ],
    "ecld-pynithy": [
        "0x439945BE73fD86fcC172179021991E96Beff3Cc4",
        "0x689f3806874d3c8A973f419a4eB24e6fBA7E830F",
        "https://polygon-rpc.com",
        137,
    ],
    "ecld-nodenithy": [
        "0x439945BE73fD86fcC172179021991E96Beff3Cc4",
        "0x689f3806874d3c8A973f419a4eB24e6fBA7E830F",
        "https://polygon-rpc.com",
        137,
    ],
}

def run_command(command, redirect_output=False):
    """
    Execute a shell command without producing output on the terminal.
    """

    stdout = subprocess.DEVNULL if redirect_output else None  # Redirect standard output to devnull
    stderr = subprocess.DEVNULL if redirect_output else None

    result = subprocess.run(command, stdout=stdout, stderr=stderr, text=True, shell=True)

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
        # Run the 'docker info' command and capture the output
        result = subprocess.check_output("docker info", text=True, stderr=subprocess.DEVNULL)

        # Find the Server section in the output
        server_info_started = False
        server_info = []
        
        for line in result.splitlines():
            if server_info_started:
                if line.strip() == "":  # End of Server section
                    break
                server_info.append(line.strip())
            elif line.startswith("Server:"):
                server_info_started = True
                server_info.append(line.strip())
        if len(server_info) > 10:
            #subprocess.check_output("docker stop las", text=True, stderr=subprocess.DEVNULL)
            #subprocess.check_output("docker rm las", text=True, stderr=subprocess.DEVNULL)
            return True
        return False
    except subprocess.CalledProcessError as e:
        return False
    except FileNotFoundError:
        return False

def clean_up_registry():
    # Remove the 'registry' directory if it exists
    shutil.rmtree("./registry", ignore_errors=True)

    # Stop and remove any running Docker containers or images that might conflict
    dockerPS = get_command_output("docker ps --filter name=registry -q", True)
    if dockerPS:
        run_command(f'docker stop {" ".join(dockerPS.splitlines())}', True)

    remainingContainers = get_command_output("docker ps --filter 'name=etny' -a -q", True)
    if remainingContainers:
        run_command(f"docker stop {remainingContainers} -f", True)

    remainingContainers = get_command_output("docker ps --filter 'name=las' -a -q", True)
    if remainingContainers:
        run_command(f"docker stop {remainingContainers} -f", True)

    dockeri = get_command_output("docker ps --filter name=las -q", True)
    if dockeri:
        run_command(f'docker stop {" ".join(dockeri.splitlines())}', True)

    dockerRm = get_command_output("docker ps --filter name=registry -q", True)
    if dockerRm:
        run_command(f'docker rm {" ".join(dockerRm.splitlines())} -f', True)

    dockerImg = get_command_output('docker images --filter reference="*etny*" -q', True)
    if dockerImg:
        run_command(f'docker rmi {" ".join(dockerImg.splitlines())} -f', True)

    dockerImgReg = get_command_output(
        'docker images --filter reference="*registry*" -q', True
    )
    if dockerImgReg:
        run_command(f'docker rmi {" ".join(dockerImgReg.splitlines())} -f', True)

def copy_backend_to_build_dir(build_dir):
    # Copy serverless source code to the build directory

    src_dir = "./src/serverless"
    dest_dir = os.path.join(build_dir, "securelock", "src", "serverless")
    #print(f"Creating destination directory: {dest_dir}")
    os.makedirs(dest_dir, exist_ok=True)

    #print(f"Copying files from {src_dir} to {dest_dir}")
    for file_name in os.listdir(src_dir):
        src_file = os.path.join(src_dir, file_name)
        dest_file = os.path.join(dest_dir, file_name)
        if os.path.isfile(src_file):
            shutil.copy(src_file, dest_file)

    return True

def update_dockerfile():
    PROJECT_NAME = config.read("PROJECT_NAME")
    BLOCKCHAIN_NETWORK = config.read("BLOCKCHAIN_NETWORK")
    VERSION = config.read("VERSION")
    TRUSTED_ZONE_IMAGE = config.read("TRUSTED_ZONE_IMAGE")

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
        dockerfile_secure_template.replace("__IMAGE_PATH__", TRUSTED_ZONE_IMAGE)
    )

    with open("Dockerfile.base", "w") as f:
        f.write(dockerfile_secure_content)

    return True


def start_local_registry():
    # Set up Docker registry
    run_command("docker pull registry:2", True)
    run_command("docker run -d --restart=always -p 5000:5000 --name registry registry:2", True)

    return True

def main():
    global current_dir
    # Set current directory
    current_dir = os.getcwd()
    # Set the build directory path
    build_dir = Path(__file__).resolve().parent / "build"
    spinner = Spinner()
    

    VERSION = config.read("VERSION")
    PROJECT_NAME = config.read("PROJECT_NAME")
    BLOCKCHAIN_NETWORK = config.read("BLOCKCHAIN_NETWORK")
    TRUSTED_ZONE_IMAGE = config.read("TRUSTED_ZONE_IMAGE")

    
    isMainnet = False if "testnet" in TRUSTED_ZONE_IMAGE.lower() else True

    dockerPS = spinner.spin_till_done("Checking docker service", get_docker_server_info)

    if dockerPS == None:
        print("""
\t\tDocker service is not running. Please start docker to continue.
\t\tMore information about installing and running Docker can be founde here: https://docs.docker.com/engine/install/
""")
        exit(1)
  

    spinner.spin_till_done("Cleanup local registry", get_docker_server_info)

    spinner.spin_till_done("Copy backend files from src to build directory", copy_backend_to_build_dir, build_dir)

    spinner.spin_till_done("Start local registry", start_local_registry)

    
    # Change directory to the build directory
    
    os.chdir(build_dir)

    spinner.spin_till_done("Update dockerfile ", update_dockerfile)

    SECURELOCK_SESSION = config.read("SECURELOCK_SESSION")
    
    # Adding dockerfile customizations

    # Build and push Docker image for etny-securelock-base

    print()
    print(f"\u276f\u276f Building base image")
    print()

    run_command("docker build -f Dockerfile.base -t etny-securelock-base:latest .")
    #run_command("docker push localhost:5000/etny-securelock-base:latest")


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

    dockerfile_secure_content = (
        dockerfile_secure_template.replace(
            "__SECURELOCK_SESSION__", SECURELOCK_SESSION
        )
        .replace("__BUCKET_NAME__", TRUSTED_ZONE_IMAGE + "-v3")
        .replace(
            "__SMART_CONTRACT_ADDRESS__",
            ECRunner[TRUSTED_ZONE_IMAGE][0],
        )
        .replace("__IMAGE_REGISTRY_ADDRESS__", ECRunner[TRUSTED_ZONE_IMAGE][1])
        .replace("__RPC_URL__", ECRunner[TRUSTED_ZONE_IMAGE][2])
        .replace("__CHAIN_ID__", str(ECRunner[TRUSTED_ZONE_IMAGE][3]))
        .replace("__TRUSTED_ZONE_IMAGE__", TRUSTED_ZONE_IMAGE)
        .replace("__IMAGE_PATH__", TRUSTED_ZONE_IMAGE)
    )
    imagesTag = BLOCKCHAIN_NETWORK.lower()
    if isMainnet:
        imagesTag = BLOCKCHAIN_NETWORK.lower().split("_")[0]
        dockerfile_secure_content.replace(
            "# RUN scone-signer sign", "RUN scone-signer sign"
        )

    with open("Dockerfile", "w") as f:
        f.write(dockerfile_secure_content)


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

    print(f"TRUSTED_ZONE_IMAGE: {TRUSTED_ZONE_IMAGE}")
    config.write("TRUSTED_ZONE_IMAGE", TRUSTED_ZONE_IMAGE)

    # Build etny-trustedzone
    print()
    print(f"\u276f\u276f Building trustedzone image")
    print()

    run_command(
        f"docker pull registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-trustedzone:py_{imagesTag}"
    )
    run_command(
        f"docker tag registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-trustedzone:py_{imagesTag} localhost:5000/etny-trustedzone"
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
        f"docker pull registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-las:py_{imagesTag}"
    )
    run_command(
        f"docker tag registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-las:py_{imagesTag} localhost:5000/etny-las"
    )

    print()
    print(f"\u276f\u276f Pushing las image to local registry")
    print()

    run_command("docker push localhost:5000/etny-las")

    print()
    print(f"\u276f\u276f Cleaning up")
    print()
    # Return to the original directory
    os.chdir(current_dir)
    run_command("docker cp registry:/var/lib/registry registry")

    shutil.rmtree(build_dir + "src/serverless", ignore_errors=True)
