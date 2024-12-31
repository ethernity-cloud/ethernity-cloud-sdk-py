import os
import sys
import shutil
import subprocess
import re
from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config
from ethernity_cloud_sdk_py.commands.pynithy.run.image_registry import ImageRegistry

config = Config(Path(".config.json").resolve())
config.load()


# For accessing package resources
try:
    from importlib.resources import path as resources_path
except ImportError:
    # For Python versions < 3.7
    from importlib_resources import path as resources_path  # type: ignore

config = None

def initialize_config(file_path):
    """
    Initialize the global config variable with the specified file path.

    Args:
        file_path (str): Path to the configuration file.
    """
    global config
    config = Config(file_path)
    config.load()
    #print("Configuration loaded:", config.config)

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
    initialize_config('.config.json')
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
        config.write("BASE_IMAGE_TAG", base_image_tag)
        config.write("DOCKER_REPO_URL", docker_repo_url)
        config.write("DOCKER_LOGIN", docker_login)
        config.write("DOCKER_PASSWORD", docker_password)
    print()
    blockchain_network_options = [
        "Bloxberg Mainnet",
        "Bloxberg Testnet",
        "Polygon Mainnet",
        "Polygon Amoy",
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

    config.write("PROJECT_NAME", project_name)
    config.write("SERVICE_TYPE", service_type)


    #image_registry = ImageRegistry()

    #print(f"Running script image_registry...")
    #print(os.getcwd())
  
    #image_registry.main(
    #    blockchain_network.replace(" ", "_"),
    #    project_name.replace(" ", "-"),
    #    "v3",
    #)

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
        custom_url = "https://ipfs.ethernity.cloud"

    os.makedirs("src/serverless", exist_ok=True)

    print()
    app_template_options = ["yes", "no"]
    use_app_template = prompt_options(
        "Do you want a 'Hello World' app template as a starting point? (default is yes): ",
        app_template_options,
        "yes",
    )

    if use_app_template == "yes":
        print("Bringing Cli/Backend templates...")
        print("  src/serverless/backend.py (Hello World function)")
        print("  src/ethernity_task.py (Hello World function call - Cli)")
        # Copy the 'src' and 'public' directories from the package to the current directory
        # We need to use package resources for this
        package_name = "ethernity_cloud_sdk_py"
        # Copy 'src' directory
        with resources_path(f"{package_name}.templates", "src") as src_path:
            shutil.copytree(
                src_path, os.path.join(os.getcwd(), "src"), dirs_exist_ok=True
            )
       
    else:
        print(
            "Define backend functions in src/serverless to be available for cli interaction."
        )

    config.write("PROJECT_NAME", project_name.replace(" ", "_"))
    config.write("SERVICE_TYPE", service_type)
    if service_type == "Custom":
        config.write("BASE_IMAGE_TAG", base_image_tag or "")
        config.write("DOCKER_REPO_URL", docker_repo_url)
        config.write("DOCKER_LOGIN", docker_login)
        config.write("DOCKER_PASSWORD", docker_password)
    elif service_type == "Nodenithy":
        config.write("BASE_IMAGE_TAG", "")
        config.write("DOCKER_REPO_URL", "")
        config.write("DOCKER_LOGIN", "")
        config.write("DOCKER_PASSWORD", "")
    elif service_type == "Pynithy":
        config.write("BASE_IMAGE_TAG", "")
        config.write("DOCKER_REPO_URL", "")
        config.write("DOCKER_LOGIN", "")
        config.write("DOCKER_PASSWORD", "")
    config.write("BLOCKCHAIN_NETWORK", blockchain_network.replace(" ", "_"))
    config.write("IPFS_ENDPOINT", custom_url)
    config.write("IPFS_TOKEN", ipfs_token or "")
    config.write("VERSION", 0)
    config.write("PREDECESSOR_HASH_SECURELOCK", "")


    # Determine the prefix based on the blockchain network
    prefix = "ecld" if "polygon" in blockchain_network.lower() else "etny"

    # Initialize suffix to empty
    suffix = ""

    # Check if it's an amoy network
    if "amoy" in blockchain_network.lower():
        suffix = "-amoy"
    # Otherwise, check if it's a testnet
    elif "testnet" in blockchain_network.lower():
        suffix = "-testnet"

    # Combine the pieces into a final image identifier
    trusted_zone_image = f"{prefix}-{service_type.lower()}{suffix}"


    # Write the result to the config
    config.write("TRUSTED_ZONE_IMAGE", trusted_zone_image)
    
    print()
    print(
        """=================================================================================================================

The customize the backend edit serverless/backend.py with your desired functions.
Please skip this step if you only want to run the helloworld example.

Now you are ready to build!
To start the build process run:

    ecld-build
        """
    )