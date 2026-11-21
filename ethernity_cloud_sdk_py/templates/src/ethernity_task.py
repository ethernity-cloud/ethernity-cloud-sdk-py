import os
import warnings
import time
from dotenv import load_dotenv
import getpass
warnings.filterwarnings("ignore")


from ethernity_cloud_runner_py.runner import EthernityCloudRunner
from ethernity_cloud_sdk_py.commands import execution
from ethernity_cloud_sdk_py.commands.private_key import PrivateKeyManager

code = "hello('World!')"

import time

def print_logs(runner):
    """Polls the runner until its task has ended, printing each step the task
    reaches (get_state()'s progress) with the task's status, as ecld-run
    does."""
    seen = None
    while runner.is_running():
        state = runner.get_state()
        if state["progress"] != seen:
            seen = state["progress"]
            print(f"[{seen}] {state['status']}")
        time.sleep(0.5)

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

    runner = EthernityCloudRunner(os.getenv("NETWORK_NAME"),os.getenv("NETWORK_TYPE"))
    runner.set_log_level("INFO")
    runner.set_private_key(PRIVATE_KEY)

    # The task's artefacts go through the bootnode's payload intake; a Kubo RPC
    # API of the dApp's own goes in set_storage_ipfs(url, token) instead, the
    # URL ending in /api/v0 and the token sent verbatim as the Authorization
    # header when that API requires one (the public API takes writes only with
    # a writer token).
    runner.set_public_intake()
    runner.connect()

    # The execution object: the EXEC_* variables ecld-publish exported to
    # src/.env from .config.json's EXECUTION block (tests/spec/
    # execution-spec.json), parsed and checked; an unset variable takes its
    # default, and an EXEC_* variable that is not one of execution.ENV_KEYS
    # is refused. It is passed nested, and its durationHours as the flat
    # "duration" a runner without the PoX 2.0 execution object reads.
    execution_config = execution.validate(execution.from_env(os.environ))

    resources = {
        # tokens; a runner without the PoX 2.0 execution object takes
        # whole tokens only
        "taskPrice": 3,
        "cpu": 1,
        "memory": 1,
        "storage": 10,
        "bandwidth": 1,
        "duration": execution_config["durationHours"],
        "validators": 1,
        "execution": execution_config,
    }

    trustedzone_enclave = os.getenv("TRUSTED_ZONE_IMAGE")
    securelock_enclave = os.getenv("PROJECT_NAME")
    # runner.run()'s version arg is the PROTOCOL version -- the runner queries the
    # image registry with getLatestImageVersionPublicKey(name, <this>), and images
    # register their "latest" pointer under the protocol version "v3". This is NOT
    # the enclave/template VERSION (e.g. "21"); passing that made the runner look
    # up the image under the wrong key and resolve a stale/missing enclave.
    securelock_protocol_version = "v3"

    runner.run(
        resources,
        securelock_enclave,
        securelock_protocol_version,
        code,
        "",
        trustedzone_enclave
    )

    print_logs(runner)
    
    state = runner.get_state()

    if state['status'] == "ERROR":
        print(f"Error: {state['last_error']}")
        # A task the enclave reported with a non-zero task code keeps its
        # result: the value is the enclave's error report.
        result = runner.get_result()
        if result:
            print(result['value'])
        print(f"Processed Events: {state['processed_events']}, Remaining Events: {state['remaining_events']}")
        
    elif state['status'] == "SUCCESS":    
        result = runner.get_result()
        print(result['value'])

if __name__ == "__main__":
    execute_task(code)