import os
import sys
import shutil
import subprocess
import re
from pathlib import Path
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks, dAppTypes, TemplateConfig
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
            print(f"Invalid option '{reply}'. Please select a valid number.")

def display_options(options):
    """
    Display the list of options to the user.
    """
    for idx, option in enumerate(options, start=1):
        print(f"{idx}. {option}")



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

    config.write("PROJECT_NAME", project_name.replace(" ", "_"))

    # Step 2: Extract Display Options from the Dictionary Keys
    display_options_list = BlockchainNetworks.get_display_options()

    # Define the default display option
    default_display_option = "Bloxberg Testnet"

    prompt_message = (
        "On which Blockchain network do you want to have the dApp deployed up, as a starting point? "
        f"(default is '{default_display_option}'): "
    )

    selected_display_option = prompt_options(
        prompt_message,
        display_options_list,
        default_display_option,
    )


    BLOCKCHAIN_ID = BlockchainNetworks.get_enum_name(selected_display_option)


    BLOCKCHAIN_CONFIG = BlockchainNetworks.get_network_details(selected_display_option)

    config.write("BLOCKCHAIN_NETWORK", BLOCKCHAIN_ID)
    print()

    print(
        f"Checking if the project named {project_name} is available on the {BLOCKCHAIN_CONFIG.display_name} network and ownership..."
    )


    dapp_types_options = [dAppType.value for dAppType in dAppTypes]
    default_dApp_type = "Pynithy"

    dApp_prompt_message = (
        "Select the type of code to be run during the compute layer "
        f"(default is {default_dApp_type}): "
    )

    selected_dApp_type = prompt_options(
        dApp_prompt_message,
        dapp_types_options,
        default_dApp_type,
    )

    TEMPLATE_CONFIG = BLOCKCHAIN_CONFIG.template_image.get(selected_dApp_type)

    # Step 7: Handle Docker Variables Based on dApp Type
    docker_repo_url = docker_login = docker_password = base_image_tag = None
    if selected_dApp_type == dAppTypes.CUSTOM.value:

        # Prompt for Base Image Tag
        trusted_zone_image = input("Enter the trusted zone image name: ").strip()
        while not base_image_tag:
            print("Trusted zone image name cannot be empty.")
            trusted_zone_image = input("Enter the trusted zone inmage name: ").strip()

        # Prompt for Docker Repository URL with validation
        while True:
            docker_repo_url = input("Enter Docker repository URL: ").strip()
            if is_valid_url(docker_repo_url):
                break
            else:
                print("Invalid URL format. Please enter a valid Docker repository URL (e.g., https://repo.url).")
        
        # Prompt for Base Image Tag
        base_image_tag = input("Enter the image tag: ").strip()
        while not base_image_tag:
            print("Base image tag cannot be empty.")
            base_image_tag = input("Enter the image tag: ").strip()
        
        # Prompt for Docker Login (username)
        docker_login = input("Enter Docker Login (username): ").strip()
        while not docker_login:
            print("Docker Login cannot be empty.")
            docker_login = input("Enter Docker Login (username): ").strip()
        
        # Prompt for Docker Password securely
        docker_password = getpass.getpass("Enter Docker Password: ").strip()
        while not docker_password:
            print("Docker Password cannot be empty.")
            docker_password = getpass.getpass("Enter Docker Password: ").strip()

        config.write("TRUSTED_ZONE_IMAGE", trusted_zone_image)
        config.write("BASE_IMAGE_TAG", base_image_tag)
        config.write("DOCKER_REPO_URL", docker_repo_url)
        config.write("DOCKER_LOGIN", docker_login)
        config.write("DOCKER_PASSWORD", docker_password)

    config.write("DAPP_TYPE", selected_dApp_type)

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

    config.write("IPFS_ENDPOINT", custom_url)
    config.write("IPFS_TOKEN", ipfs_token or "")
    config.write("VERSION", 0)
    config.write("PREDECESSOR_HASH_SECURELOCK", "")
    
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