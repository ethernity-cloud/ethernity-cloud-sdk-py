import os
import sys
import shutil
import subprocess
import re
from pathlib import Path
from dotenv import load_dotenv, set_key

# For accessing package resources
try:
    from importlib.resources import path as resources_path
except ImportError:
    # For Python versions < 3.7
    from importlib_resources import path as resources_path  # type: ignore


def write_env(key, value, env_file=".env"):
    """
    Write or update key-value pairs in a .env file in the current working directory.
    """
    env_path = os.path.join(os.getcwd(), env_file)
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


def get_project_name():
    """
    Prompt user for the project name.
    """
    while True:
        project_name = input("Choose a name for your project: ").strip()
        if not project_name:
            print("Project name cannot be blank. Please enter a valid name.")
        else:
            print(f"You have chosen the project name: {project_name}")
            return project_name


def display_options(options):
    for index, option in enumerate(options):
        print(f"{index + 1}) {option}")


def prompt_options(message, options, default_option):
    """
    Prompt the user to select an option.
    """
    while True:
        display_options(options)
        reply = input(message).strip()
        if not reply:
            print(f"No option selected. Defaulting to {default_option}.")
            return default_option
        elif reply.isdigit() and 1 <= int(reply) <= len(options):
            return options[int(reply) - 1]
        else:
            print(f"Invalid option {reply}. Please select a valid number.")


def print_intro():
    intro = """
        ╔───────────────────────────────────────────────────────────────────────────────────────────────────────────────╗
        │                                                                                                               │
        │        .... -+++++++. ....                                                                                    │
        │     -++++++++-     .++++++++.      _____ _   _                     _ _             ____ _                 _   │
        │   .++-     ..    .++-     .++-    | ____| |_| |__   ___ _ __ _ __ (_) |_ _   _    / ___| | ___  _   _  __| |  │
        │  --++----      .++-         ...   |  _| | __| '_ \\ / _ \\ '__| '_ \\| | __| | | |  | |   | |/ _ \\| | | |/ _` |  │
        │  --++----    .++-.          ...   | |___| |_| | | |  __/ |  | | | | | |_| |_| |  | |___| | (_) | |_| | (_| |  │
        │   .++-     .+++.    .     .--.    |_____|\\__|_| |_|\\___|_|  |_| |_|_|\\__|\\__, |   \\____|_|\\___/ \\__,_|\\__,_|  │
        │     -++++++++.    .---------.                                            |___/                                │
        │        .... .-------. ....                                                                                    │
        │                                                                                                               │
        ╚───────────────────────────────────────────────────────────────────────────────────────────────────────────────╝
                                              Welcome to the Ethernity Cloud SDK

           The Ethernity Cloud SDK is a comprehensive toolkit designed to facilitate the development and management of
          decentralized applications (dApps) and serverless binaries on the Ethernity Cloud ecosystem. Geared towards
          developers proficient in Python or Node.js, this toolkit aims to help you effectively harness the key features
          of the ecosystem, such as data security, decentralized processing, and blockchain-driven transparency and
          trustless model for real-time data processing.
        """
    print(intro)


def main():
    print_intro()
    project_name = get_project_name()
    print()
    service_type_options = ["Pynithy", "Custom"]  # "Nodenithy",
    service_type = prompt_options(
        "Select the type of code to be ran during the compute layer (default is Pynithy): ",
        service_type_options,
        "Pynithy",
    )

    docker_repo_url = docker_login = docker_password = base_image_tag = None
    if service_type == "Custom":
        docker_repo_url = input("Enter Docker repository URL: ").strip()
        docker_login = input("Enter Docker Login (username): ").strip()
        docker_password = input("Enter Password: ").strip()
        base_image_tag = input("Enter the image tag: ").strip()
    print()
    blockchain_network_options = [
        "Bloxberg Mainnet",
        "Bloxberg Testnet",
        "Polygon Mainnet",
        "Polygon Amoy Testnet",
    ]
    blockchain_network = prompt_options(
        "On which Blockchain network do you want to have the app set up, as a starting point? (default is Bloxberg Testnet): ",
        blockchain_network_options,
        "Bloxberg Testnet",
    )
    print()

    print(
        f"Checking if the project name (image name) is available on the {blockchain_network.replace(' ', '_')} network and ownership..."
    )
    import ethernity_cloud_sdk_py.commands.pynithy.run.image_registry as image_registry

    # Execute the external script (image_registry.py)
    # script_path = (
    #     Path(__file__).resolve().parent / "pynithy" / "run" / "image_registry.py"
    # )
    print(f"Running script image_registry...")
    print(os.getcwd())
    # if not script_path.exists():
    #     print(f"Error: Script {script_path} not found.")
    #     sys.exit(1)

    # try:
    #     subprocess.run(
    #         [
    #             "python",
    #             str(script_path),
    #             blockchain_network.replace(" ", "_"),
    #             project_name.replace(" ", "-"),
    #             "v3",
    #         ],
    #         check=True,
    #     )
    # except subprocess.CalledProcessError as e:
    #     print(f"Error executing script {script_path}")
    #     sys.exit(1)
    image_registry.main(
        blockchain_network.replace(" ", "_"),
        project_name.replace(" ", "-"),
        "v3",
    )

    print()
    ipfs_service_options = ["Ethernity (best effort)", "Custom IPFS"]
    ipfs_service = prompt_options(
        "Select the IPFS pinning service you want to use (default is Ethernity): ",
        ipfs_service_options,
        "Ethernity (best effort)",
    )

    custom_url = ipfs_token = None
    if ipfs_service == "Custom IPFS":
        custom_url = input(
            "Enter the endpoint URL for the IPFS pinning service you want to use: "
        ).strip()
        ipfs_token = input(
            "Enter the access token to be used when calling the IPFS pinning service: "
        ).strip()
    else:
        custom_url = "http://ipfs-sdk.ethernity.cloud:5001"

    os.makedirs("src/serverless", exist_ok=True)

    print()
    app_template_options = ["yes", "no"]
    use_app_template = prompt_options(
        "Do you want a 'Hello World' app template as a starting point? (default is yes): ",
        app_template_options,
        "yes",
    )

    if use_app_template == "yes":
        print("Bringing Frontend/Backend templates...")
        print("  src/serverless/backend.js (Hello World function)")
        print("  src/ec_helloworld_example.js (Hello World function call - Frontend)")
        # Copy the 'src' and 'public' directories from the package to the current directory
        # We need to use package resources for this
        package_name = "ethernity_cloud_sdk_py"
        # Copy 'src' directory
        with resources_path(f"{package_name}.templates", "src") as src_path:
            shutil.copytree(
                src_path, os.path.join(os.getcwd(), "src"), dirs_exist_ok=True
            )
        # Simulate copying files
        # script_dir = os.path.dirname(os.path.abspath(__file__))

        # source_src = os.path.join(script_dir, "src")
        # target_src = "src/"
        # # source_public = os.path.join(script_dir, "public")
        # # target_public = "public/"

        # shutil.copytree(source_src, target_src, dirs_exist_ok=True)
        # shutil.copytree(source_public, target_public, dirs_exist_ok=True)
        # print("Installing required packages...")
        # # Simulate npm install
        # try:
        #     subprocess.run(
        #         [
        #             "npm",
        #             "install",
        #             "@ethernity-cloud/runner@0.0.26",
        #             "@testing-library/jest-dom@5.17.0",
        #             "@testing-library/react@13.4.0",
        #             "@testing-library/user-event@13.5.0",
        #             "react@18.3.1",
        #             "react-dom@18.3.1",
        #             "react-scripts@5.0.1",
        #             "web-vitals@2.1.4",
        #             "web3@4.9.0",
        #             "dotenv@16.4.5",
        #         ],
        #         check=True,
        #     )
        # except subprocess.CalledProcessError as e:
        #     print("Error installing npm packages.")
        #     sys.exit(1)
    else:
        print(
            "Define backend functions in src/serverless to be available for Frontend interaction."
        )

    write_env("PROJECT_NAME", project_name.replace(" ", "_"))
    write_env("SERVICE_TYPE", service_type)
    if service_type == "Custom":
        write_env("BASE_IMAGE_TAG", base_image_tag or "")
        write_env("DOCKER_REPO_URL", docker_repo_url)
        write_env("DOCKER_LOGIN", docker_login)
        write_env("DOCKER_PASSWORD", docker_password)
    elif service_type == "Nodenithy":
        write_env("BASE_IMAGE_TAG", "")
        write_env("DOCKER_REPO_URL", "")
        write_env("DOCKER_LOGIN", "")
        write_env("DOCKER_PASSWORD", "")
    elif service_type == "Pynithy":
        write_env("BASE_IMAGE_TAG", "")
        write_env("DOCKER_REPO_URL", "")
        write_env("DOCKER_LOGIN", "")
        write_env("DOCKER_PASSWORD", "")
    write_env("BLOCKCHAIN_NETWORK", blockchain_network.replace(" ", "_"))
    write_env("IPFS_ENDPOINT", custom_url)
    write_env("IPFS_TOKEN", ipfs_token or "")
    write_env("VERSION", "v1")

    write_env(
        "TRUSTED_ZONE_IMAGE",
        f"{'ecld' if 'polygon' in blockchain_network.lower() else 'etny'}-{service_type.lower()}-{'testnet' if 'testnet' in blockchain_network.lower() else ''}",
    )

    print()
    print(
        "You can proceed to run ecld-build, once you set up the serverless functions or directly if you want to test with the provided template."
    )


if __name__ == "__main__":
    main()
