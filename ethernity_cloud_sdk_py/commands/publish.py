import os
import sys
import subprocess
import getpass
from dotenv import load_dotenv
from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config
from ethernity_cloud_sdk_py.commands.private_key import PrivateKeyManager
from ethernity_cloud_sdk_py.commands.pynithy.run.image_registry import ImageRegistry
from ethernity_cloud_sdk_py.commands.spinner import Spinner

config = Config(Path(".config.json").resolve())
config.load()

envfile = Path("src/.env").resolve()

# For accessing package resources
try:
    from importlib.resources import path as resources_path
except ImportError:
    # For Python versions < 3.7
    from importlib_resources import path as resources_path  # type: ignore



def prompt(question, default_value=None):
    """
    Prompt user for input with an optional default value.
    """
    if default_value:
        question = f"{question} (default value: {default_value}) "
    else:
        question = f"{question} "
    user_input = input(question).strip()
    if not user_input and default_value is not None:
        return default_value
    return user_input


def prompt_options(message, options, default_option):
    while True:
        # Print the prompt and wait for user input
        user_input = input(f"{message} ").strip().lower()
        
        # If user presses Enter without input, display the default inline
        if not user_input:
            # Move the cursor up one line: \033[A
            # Then rewrite the line, this time showing the chosen default
            print(f"\033[A{message} {default_option}")
            return default_option
        elif user_input in options:
            return user_input
        else:
            print(
                f'\n\t\t\tInvalid option "{user_input}".\n\t\t\tPlease enter one of: {", ".join(options)}. Default value is: {default_option}\n'
            )

            
def write_env(key, value, env_file=envfile):
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

def main():
    global current_dir
    # Set current directory
    current_dir = os.getcwd()

    PROJECT_NAME = config.read("PROJECT_NAME")
    BLOCKCHAIN_NETWORK = config.read("BLOCKCHAIN_NETWORK")
    ENC_PRIVATE_KEY = config.read("ENC_PRIVATE_KEY")
    DEVELOPER_FEE = config.read("DEVELOPER_FEE")
    SERVICE_TYPE = config.read("SERVICE_TYPE")
    VERSION = config.read("VERSION")
    WALLET_ADDRESS = config.read("WALLET_ADDRESS")
    IPFS_HASH = config.read("IPFS_HASH")

    spinner = Spinner()

    if not ENC_PRIVATE_KEY or not WALLET_ADDRESS:
        has_wallet = prompt(
            "Do you have an existing wallet?", default_value="yes"
        ).lower()

        if has_wallet != "yes":
            print("Without a wallet, you will not be able to publish.")
            print(
                "Please refer to Blockchain Wallets Documentation (https://docs.ethernity.cloud/ethernity-node/prerequisites-ethernity-node/blockchain-wallets)."
            )
            exit(1)

        while True:
            PRIVATE_KEY = prompt("Enter your private key:")
            try:
                result = image_registry(str(PRIVATE_KEY))
                break
            except Exception as e:
                print(f"Unable to load private key: {e}")
                continue

        PASSWORD = getpass.getpass("Set a password for your private key:")

        pkm = PrivateKeyManager(PASSWORD)
        ENC_PRIVATE_KEY = pkm.encrypt_private_key(PRIVATE_KEY)
        config.write("ENC_PRIVATE_KEY", ENC_PRIVATE_KEY)
        WALLET_ADDRESS = pkm.extract_address_from_private_key(PRIVATE_KEY)
        config.write("WALLET_ADDRESS", WALLET_ADDRESS)
        print()
    else:
        while True:
            try:
                PASSWORD = getpass.getpass("Enter your private key password:")
                ENC_PRIVATE_KEY = config.read("ENC_PRIVATE_KEY")
                pkm = PrivateKeyManager(PASSWORD)
                PRIVATE_KEY = pkm.decrypt_private_key(ENC_PRIVATE_KEY)
                break
            except Exception as e:
                print("Incorrect password. Please try again.")
                continue

    image_registry = ImageRegistry(PRIVATE_KEY)


    
    print("\n\u276f\u276f Initializing")

    if image_registry == None:
        print(
            "\u2718 Unable to initialize blockchain network."
        )
        exit(1)

    try:
        result = spinner.spin_till_done("Checking gas balance", image_registry.check_balance)
    except Exception as e:
        print(f"\t\u2714  Error checking gas balance: {e}")
        exit(1)

    if float(result) < 0.001:
        print("\t\u2718  Insufficient gas. Please make sure you have enough gas to deploy the service.")
        if BLOCKCHAIN_NETWORK=="Bloxberg_Testnet" or BLOCKCHAIN_NETWORK=="Bloxberg_Mainnet":
            print ("""
        Please use the faucet here to fill your wallet with BERGs:
                                    
        https://faucet.bloxberg.org
""")
        if BLOCKCHAIN_NETWORK=="Polygon_Testnet" or BLOCKCHAIN_NETWORK=="Polygon_Mainnet":
            print ("""  
        Please fill the wallet wit at least 0.001 POL
""")
        exit(1) 
        
    result = None
    result = spinner.spin_till_done("Checking enclave ownership", image_registry.check_image_permissions)

    if not result:
        print("\t\u2714 Enclave ownership verification failed.")
        print()
        print(
            "\tPlease ensure the assigned wallet is the owner of the enclave image with the current version"
        )
        exit(1)

    #print(result)

    if IPFS_HASH != "":
        #print(f"# Checking if IPFS hash '{IPFS_HASH}' exists...")
        try:

            IMAGE_DETAILS = image_registry.get_image_details(IPFS_HASH)
            REGISTRY_ENCLAVE_HASH  = IMAGE_DETAILS.ipfs_hash
            REGISTRY_PUBLIC_KEY = IMAGE_DETAILS.public_key
            REGISTRY_DOCKER_COMPOSE_HASH = IMAGE_DETAILS.docker_compose_hash

            #print(REGISTRY_ENCLAVE_HASH, REGISTRY_PUBLIC_KEY, REGISTRY_DOCKER_COMPOSE_HASH)
            if REGISTRY_ENCLAVE_HASH != "":
                #print(f"IPFS hash '{IPFS_HASH}' exists.")
                #print(f"Registry Enclave Hash: {REGISTRY_ENCLAVE_HASH}")
                #print(f"Registry Public Key: {REGISTRY_PUBLIC_KEY}")
                #print(f"Registry Docker Composer Hash: {REGISTRY_DOCKER_COMPOSE_HASH}")
                #print()
                print(f"\t\033[91m\u2718\033[0m  The enclave is already published\n\n\t   If you have made modifications to the backend, you should run ecld-build\n")
                options = ["Y", "yes", "no", "n"]

                build = prompt_options(
                    f"\t\tDo you want to run `ecld-build` right now? [Y/n]:",
                    options,
                    "y",
                )


                if (build.lower() == "y" or build == "yes"):
                    print()
                    try:
                        subprocess.run(["ecld-build"], check=True)
                    except subprocess.CalledProcessError:
                        print("Error building the enclave image.")
                        exit(1)
                else:
                    print("\u276f\u276f Exiting...")
                    exit(1)
                
        except Exception as e:
            print(f"Error checking image: {e}")
            exit(1)
    


    DEVELOPER_FEE = config.read("DEVELOPER_FEE")

    if not DEVELOPER_FEE:
        task_percentage = prompt(
            "Each time this enclave runs, you will be rewarded with a percentage of the execution price.\n\nPlease specify the percentage.",
            default_value="10",
        )
        config.write("DEVELOPER_FEE", task_percentage)


    if SERVICE_TYPE == "Nodenithy":
        print("Adding prerequisites for Nodenithy...")
        run_script_path = Path(__file__).resolve().parent / "nodenithy" / "run.py"
        try:
            subprocess.run(["python", str(run_script_path)], check=True)
        except subprocess.CalledProcessError:
            print("Error running the Nodenithy run script.")
            exit(1)
    elif SERVICE_TYPE == "Pynithy":
        import ethernity_cloud_sdk_py.commands.pynithy.publish as publishScript

        try:
            publishScript.main(PRIVATE_KEY)
            print("")
        except Exception as e:
            print(f"Error running the publish script: {e}")
            exit(1)

        options = ["y", "n", "yes", "no"]
        export = prompt_options(
            f"\t\tDo you want to export the runtime variables to '{envfile}'? [Y/n]:",
            options,
            "y",
        )


        if export.lower() == 'y' or export.lower() == 'yes':
            options = ["y", "n", "yes", "no"]
            private_key = prompt_options(
                "\t\tDo you want to save the private key encrypted? [Y/n]: ",
                options,
                "y",
            )
        else:
            exit(0)

        write_env("PROJECT_NAME", PROJECT_NAME)
        write_env("VERSION", VERSION)
        write_env("TRUSTED_ZONE_IMAGE", config.read("TRUSTED_ZONE_IMAGE"))

        if private_key == 'y':
            write_env("ENC_PRIVATE_KEY", ENC_PRIVATE_KEY)
        else:
            write_env("PRIVATE_KEY", PRIVATE_KEY)



        print("""
\t\tYour enclave containing the backend funcions was published successfully!
\t\tYou can run the example application like this:

\t\t\tpython src/ethernity_task.py
            """
        )

    else:
        print("Something went wrong")
        exit(1)