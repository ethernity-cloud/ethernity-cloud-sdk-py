import os
import sys
import subprocess
from dotenv import load_dotenv
from pathlib import Path

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


def main():
    global current_dir
    # Set current directory
    current_dir = os.getcwd()
    dotenv_path = os.path.join(current_dir, ".env")
    load_dotenv(dotenv_path=dotenv_path)
    PROJECT_NAME = os.getenv("PROJECT_NAME")
    BLOCKCHAIN_NETWORK = os.getenv("BLOCKCHAIN_NETWORK")
    PRIVATE_KEY = os.getenv("PRIVATE_KEY")
    DEVELOPER_FEE = os.getenv("DEVELOPER_FEE")
    SERVICE_TYPE = os.getenv("SERVICE_TYPE")
    VERSION = os.getenv("VERSION")

    imageRegistryPy_path = (
        Path(__file__).resolve().parent / "pynithy" / "run" / "image_registry.py"
    )
    result = ""

    import ethernity_cloud_sdk_py.commands.pynithy.run.image_registry as image_registry

    if not (PROJECT_NAME and BLOCKCHAIN_NETWORK and PRIVATE_KEY and DEVELOPER_FEE):
        has_wallet = prompt(
            "Do you have an existing wallet?", default_value="no"
        ).lower()
        print()
        if has_wallet != "yes":
            print("Without a wallet, you will not be able to publish.")
            print(
                "Please refer to Blockchain Wallets Documentation (https://docs.ethernity.cloud/ethernity-node/prerequisites-ethernity-node/blockchain-wallets)."
            )
            sys.exit(1)

        private_key = prompt("Enter your private key:")
        # try:
        #     result = (
        #         subprocess.check_output(
        #             [
        #                 "python",
        #                 str(imageRegistryPy_path),
        # "",
        # "",
        # "",
        # str(private_key),
        # "validateAddress",
        #             ]
        #         )
        #         .decode()
        #         .strip()
        #     )
        # except subprocess.CalledProcessError as e:
        #     result = ""
        try:
            result = image_registry.main(
                "", "", "", str(private_key), "validateAddress"
            )
        except Exception as e:
            print(e)
            result = ""

        while result != "OK":
            print(result)
            private_key = prompt(
                "Invalid private key. Please enter a valid private key:"
            )
            # try:
            #     result = (
            #         subprocess.check_output(
            #             [
            #                 "python",
            #                 str(imageRegistryPy_path),
            #                 "",
            #                 "",
            #                 "",
            #                 str(private_key),
            #                 "validateAddress",
            #             ]
            #         )
            #         .decode()
            #         .strip()
            #     )
            # except subprocess.CalledProcessError as e:
            #     result = ""
            try:
                result = image_registry.main(
                    "", "", "", str(private_key), "validateAddress"
                )
            except Exception as e:
                print(e)
                result = ""

        print("Inputted Private key is valid.")
        write_env("PRIVATE_KEY", private_key)
        print()
        print("Checking blockchain for required funds...")
        # try:
        #     result = (
        #         subprocess.check_output(
        #             [
        #                 "python",
        #                 str(imageRegistryPy_path),
        #                 "",
        #                 "",
        #                 "",
        #                 str(private_key),
        #                 "checkBalance",
        #             ]
        #         )
        #         .decode()
        #         .strip()
        #     )
        # except subprocess.CalledProcessError as e:
        #     result = ""
        try:
            result = image_registry.main("", "", "", str(private_key), "checkBalance")
        except Exception as e:
            print(e)
            result = ""

        print(f"Available funds: {result}")

        print()
        print(
            f"Checking if project name is available on {BLOCKCHAIN_NETWORK} network and ownership..."
        )
        # try:
        #     result = (
        #         subprocess.check_output(
        #             [
        #                 "python",
        #                 str(imageRegistryPy_path),
        #                 str(BLOCKCHAIN_NETWORK),
        #                 str(PROJECT_NAME),
        #                 str(VERSION),
        #                 str(private_key),
        #             ],
        #             stderr=subprocess.STDOUT,
        #         )
        #         .decode()
        #         .strip()
        #     )
        # except subprocess.CalledProcessError as e:
        #     print(e)
        #     result = ""
        try:
            result = image_registry.main(
                str(BLOCKCHAIN_NETWORK),
                str(PROJECT_NAME),
                str(VERSION),
                str(private_key),
            )
        except Exception as e:
            print(e)
            result = ""

        print(result)
        if "You are not the account holder of the image" in str(result):
            exit()

        print()
        task_percentage = prompt(
            "Please specify the % of a task which will be transferred to your wallet upon successful execution",
            default_value="10",
        )
        write_env("DEVELOPER_FEE", task_percentage)
    else:
        print(
            "Using PROJECT_NAME, BLOCKCHAIN_NETWORK, PRIVATE_KEY, DEVELOPER_FEE from .env"
        )
        print("Checking blockchain for required funds...")
        # try:
        #     result = (
        #         subprocess.check_output(
        #             [
        #                 "python",
        #                 str(imageRegistryPy_path),
        #                 "",
        #                 "",
        #                 "",
        #                 "",
        #                 "checkBalance",
        #             ]
        #         )
        #         .decode()
        #         .strip()
        #     )
        # except subprocess.CalledProcessError as e:
        #     result = ""
        try:
            result = image_registry.main("", "", "", str(private_key), "checkBalance")
        except Exception as e:
            print(e)
            result = ""

        print(f"Available funds: {result}")
        print()
        private_key = PRIVATE_KEY
        # try:
        #     result = (
        #         subprocess.check_output(
        #             [
        #                 "python",
        #                 str(imageRegistryPy_path),
        #                 "",
        #                 "",
        #                 "",
        #                 str(private_key),
        #                 "validateAddress",
        #             ]
        #         )
        #         .decode()
        #         .strip()
        #     )
        # except subprocess.CalledProcessError as e:
        #     result = ""
        try:
            result = image_registry.main(
                "", "", "", str(private_key), "validateAddress"
            )
        except Exception as e:
            print(e)
            result = ""

        while result != "OK":
            print(result)
            private_key = prompt(
                "Invalid private key. Please enter a valid private key:"
            )
            # try:
            #     result = (
            #         subprocess.check_output(
            #             [
            #                 "python",
            #                 str(imageRegistryPy_path),
            #                 "",
            #                 "",
            #                 "",
            #                 str(private_key),
            #                 "validateAddress",
            #             ]
            #         )
            #         .decode()
            #         .strip()
            #     )
            # except subprocess.CalledProcessError as e:
            #     result = ""
            try:
                result = image_registry.main(
                    "", "", "", str(private_key), "validateAddress"
                )
            except Exception as e:
                print(e)
                result = ""

        write_env("PRIVATE_KEY", private_key)
        print()
        print(
            f"Checking if project name is available on {BLOCKCHAIN_NETWORK} network and ownership..."
        )
        # try:
        #     result = (
        #         subprocess.check_output(
        #             [
        #                 "python",
        #                 str(imageRegistryPy_path),
        #                 str(BLOCKCHAIN_NETWORK),
        #                 str(PROJECT_NAME),
        #                 str(VERSION),
        #                 str(private_key),
        #             ],
        #             stderr=subprocess.STDOUT,
        #         )
        #         .decode()
        #         .strip()
        #     )
        # except subprocess.CalledProcessError as e:
        #     print(e)
        #     result = ""
        try:
            result = image_registry.main(
                str(BLOCKCHAIN_NETWORK),
                str(PROJECT_NAME),
                str(VERSION),
                str(private_key),
            )
        except Exception as e:
            print(e)
            result = ""
        print(result)
        if "You are not the account holder of the image" in str(result):
            exit()

    print()
    if SERVICE_TYPE == "Nodenithy":
        print("Adding prerequisites for Nodenithy...")
        run_script_path = Path(__file__).resolve().parent / "nodenithy" / "run.py"
        try:
            subprocess.run(["python", str(run_script_path)], check=True)
        except subprocess.CalledProcessError:
            print("Error running the Nodenithy run script.")
            sys.exit(1)
    elif SERVICE_TYPE == "Pynithy":
        print("Adding prerequisites for Pynithy...")
        # script_path = Path(__file__).resolve().parent / "pynithy" / "run.py"
        print(f"Running script: runScript")
        # try:
        #     subprocess.run(["python", str(script_path)], check=True)
        #     print("")
        # except subprocess.CalledProcessError:
        #     print("Error running the publish script.")
        #     sys.exit(1)
        import ethernity_cloud_sdk_py.commands.pynithy.runner as runScript

        try:
            runScript.main()
            print("")
        except Exception as e:
            print(f"Error running the publish script: {e}")
            sys.exit(1)
    else:
        print("Something went wrong")
        sys.exit(1)


if __name__ == "__main__":
    main()
