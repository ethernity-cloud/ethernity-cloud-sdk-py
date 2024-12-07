import os
import sys
import warnings
import time
from datetime import datetime
from dotenv import load_dotenv

warnings.filterwarnings("ignore")

try:
    load_dotenv(".env" if os.path.exists(".env") else ".env.config")
except ImportError as e:
    pass

from ethernity_cloud_runner_py.runner import EthernityCloudRunner

code = "hello('World!')"

def execute_task(code) -> None:
    runner = EthernityCloudRunner()
    runner.set_log_level("DEBUG")

    runner.set_private_key(os.getenv("PRIVATE_KEY"))
    runner.set_network("Bloxberg", "Testnet")
    runner.set_storage_ipfs("https://ipfs.ethernity.cloud/api/v0")

    runner.connect()

  
    resources = {
        "taskPrice": 3,
        "cpu": 4,
        "memory": 3,
        "storage": 10,
        "bandwidth": 1,
        "duration": 1,
        "validators": 1,
    }

    trustedzone_enclave = os.getenv("TRUSTED_ZONE_IMAGE")
    securelock_enclave = os.getenv("PROJECT_NAME")
    
    runner.run(
        resources,
        securelock_enclave,
        code,
        "",
        trustedzone_enclave
    )

    # Store previously printed logs
    previous_logs = set()

    while runner.is_running():
        state = runner.get_state()
        current_logs = set(state['log'])  # Current set of logs

        # Find new logs by subtracting previous_logs from current_logs
        new_logs = current_logs - previous_logs

        # If there are new logs, print them
        if new_logs:
            for log in new_logs:
                print(log)
        
        # Update previous_logs to include the current logs
        previous_logs = current_logs

        # Optional status prints
        # print(f"{datetime.now()} Task status: {state['progress']}")
        # print(f"Processed Events: {state['processed_events']}, Remaining Events: {state['remaining_events']}")    

    time.sleep(0.5)
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
