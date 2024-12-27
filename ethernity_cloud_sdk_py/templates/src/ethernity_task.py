import os
import warnings
import time
from dotenv import load_dotenv
import getpass
warnings.filterwarnings("ignore")


from ethernity_cloud_runner_py.runner import EthernityCloudRunner
from ethernity_cloud_sdk_py.commands.private_key import PrivateKeyManager 

code = "hello('World!')"

def print_logs(runner):
    previous_logs_count = 0
    while True:
        state = runner.get_state()
        current_logs = state['log']  # Assuming this is a list of logs in chronological order

        # Determine which logs are new based on how many logs we've seen before
        new_logs = current_logs[previous_logs_count:]

        # Print the new logs in the order they appear
        for log in new_logs:
            print(log)

        # Update the count of previously seen logs
        previous_logs_count = len(current_logs)

        # Check if the task has finished and all logs have been printed
        if not runner.is_running() and previous_logs_count == len(current_logs):
            break

        # Add a small delay to prevent excessive CPU usage
        time.sleep(0.1)


def execute_task(code) -> None:

    load_dotenv(override=True)

    while True:
        try:
            PASSWORD = getpass.getpass("Enter your private key password:")
            ENC_PRIVATE_KEY = os.getenv("ENC_PRIVATE_KEY")
            pkm = PrivateKeyManager(PASSWORD)
            PRIVATE_KEY = pkm.decrypt_private_key(ENC_PRIVATE_KEY)
            break
        except Exception as e:
            print("Incorrect password. Please try again.")
            continue

    runner = EthernityCloudRunner()
    runner.set_log_level("INFO")
    runner.set_private_key(PRIVATE_KEY)
    runner.set_network("Polygon", "Mainnet")

    runner.set_storage_ipfs("https://ipfs.ethernity.cloud/api/v0")
    runner.connect()

    resources = {
        "taskPrice": 3,
        "cpu": 1,
        "memory": 1,
        "storage": 10,
        "bandwidth": 1,
        "duration": 1,
        "validators": 1,
    }

    trustedzone_enclave = os.getenv("TRUSTED_ZONE_IMAGE")
    securelock_enclave = os.getenv("PROJECT_NAME")
    securelock_version = os.getenv("VERSION")
    
    runner.run(
        resources,
        securelock_enclave,
        securelock_version,
        code,
        "",
        trustedzone_enclave
    )


    print_logs(runner)

    state = runner.get_state()

    if state['status'] == "ERROR":
        for log in state['log']:
            print(log)
        print(f"Processed Events: {state['processed_events']}, Remaining Events: {state['remaining_events']}")
        
    elif state['status'] == "SUCCESS":    
        result = runner.get_result()
        print(result['value'])

if __name__ == "__main__":
    execute_task(code)