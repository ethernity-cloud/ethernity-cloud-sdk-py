import os
import sys
import shutil
import subprocess
from pathlib import Path
from dotenv import load_dotenv

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


def write_env(key, value, env_file=".env"):
    """
    Write or update key-value pairs in a .env file in the current working directory.
    """
    env_path = os.path.join(current_dir, env_file)
    if not os.path.exists(env_path):
        with open(env_path, "w") as f:
            f.write(f"{key}={value}\n")
    else:
        replaced = False
        with open(env_path, "r") as f:
            lines = f.readlines()
        with open(env_path, "w") as f:
            for line in lines:
                if line.startswith(f"{key}="):
                    f.write(f"{key}={value}\n")
                    replaced = True
                else:
                    f.write(line)
            if not replaced:
                f.write(f"{key}={value}\n")


def run_command(command, can_pass=False):
    """
    Execute a shell command.
    """
    result = subprocess.run(command, shell=True)
    if result.returncode != 0 and not can_pass:
        print(f"Error executing command: {command}")
        sys.exit(1)


def get_command_output(command):
    """
    Execute a shell command and return its output.
    """
    result = subprocess.run(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8").strip()


def main():
    load_dotenv()
    global current_dir
    # Set current directory
    current_dir = os.getcwd()
    VERSION = os.getenv("VERSION")
    print(f"Building {VERSION}")
    # Determine template name based on the blockchain network
    BLOCKCHAIN_NETWORK = os.getenv("BLOCKCHAIN_NETWORK", "Bloxberg_Testnet")
    templateName = os.getenv("TRUSTED_ZONE_IMAGE", "etny-pynithy-testnet")
    ENCLAVE_NAME_TRUSTEDZONE = templateName
    isMainnet = False if "testnet" in templateName.lower() else True

    PROJECT_NAME = os.getenv("PROJECT_NAME")

    # Remove the 'registry' directory if it exists
    shutil.rmtree("./registry", ignore_errors=True)

    # Set the build directory path
    build_dir = Path(__file__).resolve().parent / "build"

    # Stop and remove any running Docker containers or images that might conflict
    dockerPS = get_command_output("docker ps --filter name=registry -q")
    if dockerPS:
        run_command(f'docker stop {" ".join(dockerPS.splitlines())}')

    remainingContainers = get_command_output("docker ps --filter 'name=etny' -a -q")
    if remainingContainers:
        run_command(f"docker stop {remainingContainers} -f")

    remainingContainers = get_command_output("docker ps --filter 'name=las' -a -q")
    if remainingContainers:
        run_command(f"docker stop {remainingContainers} -f")

    dockeri = get_command_output("docker ps --filter name=las -q")
    if dockeri:
        run_command(f'docker stop {" ".join(dockeri.splitlines())}')

    dockerRm = get_command_output("docker ps --filter name=registry -q")
    if dockerRm:
        run_command(f'docker rm {" ".join(dockerRm.splitlines())} -f')

    dockerImg = get_command_output('docker images --filter reference="*etny*" -q')
    if dockerImg:
        run_command(f'docker rmi {" ".join(dockerImg.splitlines())} -f')

    dockerImgReg = get_command_output(
        'docker images --filter reference="*registry*" -q'
    )
    if dockerImgReg:
        run_command(f'docker rmi {" ".join(dockerImgReg.splitlines())} -f')

    run_command("docker rm registry -f", can_pass=True)

    # Copy serverless source code to the build directory
    src_dir = "./src/serverless"
    dest_dir = os.path.join(build_dir, "securelock", "src", "serverless")
    print(f"Creating destination directory: {dest_dir}")
    os.makedirs(dest_dir, exist_ok=True)

    print(f"Copying files from {src_dir} to {dest_dir}")
    for file_name in os.listdir(src_dir):
        src_file = os.path.join(src_dir, file_name)
        dest_file = os.path.join(dest_dir, file_name)
        if os.path.isfile(src_file):
            shutil.copy(src_file, dest_file)

    # Change directory to the build directory
    os.chdir(build_dir)

    # Set up Docker registry
    run_command("docker pull registry:2")
    run_command(
        "docker run -d --restart=always -p 5000:5000 --name registry registry:2"
    )

    # Generate the enclave name for securelock
    ENCLAVE_NAME_SECURELOCK = f"{PROJECT_NAME}-SECURELOCK-V3-{BLOCKCHAIN_NETWORK.split('_')[1].lower()}-{VERSION}".replace(
        "/", "_"
    ).replace(
        "-", "_"
    )
    print(f"ENCLAVE_NAME_SECURELOCK: {ENCLAVE_NAME_SECURELOCK}")
    write_env("ENCLAVE_NAME_SECURELOCK", ENCLAVE_NAME_SECURELOCK)

    # Build etny-securelock Docker image
    print("Building etny-securelock")
    os.chdir("securelock")

    # Modify Dockerfile based on the template
    with open("Dockerfile.tmpl", "r") as f:
        dockerfile_secure_template = f.read()

    dockerfile_secure_content = (
        dockerfile_secure_template.replace(
            "__ENCLAVE_NAME_SECURELOCK__", ENCLAVE_NAME_SECURELOCK
        )
        .replace("__BUCKET_NAME__", templateName + "-v3")
        .replace(
            "__SMART_CONTRACT_ADDRESS__",
            ECRunner[templateName][0],
        )
        .replace("__IMAGE_REGISTRY_ADDRESS__", ECRunner[templateName][1])
        .replace("__RPC_URL__", ECRunner[templateName][2])
        .replace("__CHAIN_ID__", str(ECRunner[templateName][3]))
        .replace("__TRUSTED_ZONE_IMAGE__", templateName)
    )
    imagesTag = BLOCKCHAIN_NETWORK.lower()
    if isMainnet:
        imagesTag = BLOCKCHAIN_NETWORK.lower().split("_")[0]
        dockerfile_secure_content.replace(
            "# RUN scone-signer sign", "RUN scone-signer sign"
        )

    with open("Dockerfile", "w") as f:
        f.write(dockerfile_secure_content)

    # Build and push Docker image for etny-securelock
    run_command(
        f"docker build --build-arg ENCLAVE_NAME_SECURELOCK={ENCLAVE_NAME_SECURELOCK} -t etny-securelock:latest ."
    )
    run_command("docker tag etny-securelock localhost:5000/etny-securelock")
    run_command("docker push localhost:5000/etny-securelock")

    # Return to the build directory
    os.chdir("..")

    print(f"ENCLAVE_NAME_TRUSTEDZONE: {ENCLAVE_NAME_TRUSTEDZONE}")
    write_env("ENCLAVE_NAME_TRUSTEDZONE", ENCLAVE_NAME_TRUSTEDZONE)

    # Build etny-trustedzone
    print("Building etny-trustedzone")

    run_command(
        f"docker pull registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-trustedzone:py_{imagesTag}"
    )
    run_command(
        f"docker tag registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-trustedzone:py_{imagesTag} localhost:5000/etny-trustedzone"
    )
    run_command("docker push localhost:5000/etny-trustedzone")

    # # Build etny-validator
    # print("Building validator")
    # os.chdir("../validator")
    # run_command("docker build -t etny-validator:latest .")
    # run_command("docker tag etny-validator localhost:5000/etny-validator")
    # run_command("docker push localhost:5000/etny-validator")

    # Build etny-las
    print("Building etny-las")
    run_command(
        f"docker pull registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-las:py_{imagesTag}"
    )
    run_command(
        f"docker tag registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ethernity/etny-las:py_{imagesTag} localhost:5000/etny-las"
    )
    run_command("docker push localhost:5000/etny-las")

    # Return to the original directory
    os.chdir(current_dir)
    run_command("docker cp registry:/var/lib/registry registry")

    # Clean up
    print("Cleaning up")
    shutil.rmtree(dest_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
