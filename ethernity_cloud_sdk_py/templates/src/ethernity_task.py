import os
import sys
from dotenv import load_dotenv

load_dotenv()

from ethernity_cloud_runner_py.runner import EthernityCloudRunner  # type: ignore


def execute_task() -> None:
    ipfs_address = "http://ipfs.ethernity.cloud:5001/api/v0"

    code = 'hello("Hello, Python World!")'

    runner = EthernityCloudRunner()
    runner.initialize_storage(ipfs_address)

    resources = {
        "taskPrice": 8,
        "cpu": 1,
        "memory": 1,
        "storage": 1,
        "bandwidth": 1,
        "duration": 1,
        "validators": 1,
    }
    # this will execute a new task using Python template and will run the code provided above
    # the code will run on the TESTNET network
    runner.run(
        os.getenv("PROJECT_NAME"),
        code,
        "0xd58f5C1834279ABD601df85b3E4b2323aDD4E75e",
        resources,
        os.getenv("ENCLAVE_NAME_TRUSTEDZONE", ""),
    )


if __name__ == "__main__":
    execute_task()
