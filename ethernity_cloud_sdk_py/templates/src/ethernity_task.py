import os
import sys
import warnings
import time
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

    runner.set_private_key(os.environ.get("PRIVATE_KEY"))
    runner.set_network("Bloxberg", "Testnet")
    runner.set_storage_ipfs("http://ipfs.ethernity.cloud/api/v0")

    runner.connect()

  
    resources = {
        "taskPrice": 3,
        "cpu": 1,
        "memory": 1,
        "storage": 1,
        "bandwidth": 1,
        "duration": 1,
        "validators": 1,
    }

    enclave = os.getenv("PROJECT_NAME")

    runner.run(
        resources,
        enclave,
        code,
    )

    while runner.is_running():
        #state = runner.get_state()
        #for log in state['log']:
        #    print(log)
        #print(f"{datetime.now()} Task status: {state['progress']}")
        #print(f"Processed Events: {state['processed_events']}, Remaining Events: {state['remaining_events']}")    
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
