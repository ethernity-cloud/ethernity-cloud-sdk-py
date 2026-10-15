import requests
import time
import sys

BASE_URL = "https://publickey.ethernity.cloud"
# How long a publish whose image is registered on chain waits for the
# service's registry watcher to queue it before posting the hash itself. The
# watcher polls the registries every 30 s; a chain row carries the publisher's
# IPFS peer, which a posted row does not.
CHAIN_ROW_GRACE_SECONDS = 180
POLL_SECONDS = 5

# When stdout is a TTY we redraw a single spinner line with a carriage return;
# in a non-TTY (log file / CI) carriage returns just overwrite invisibly and
# the progress is never flushed with a newline, so we emit plain newline lines
# instead. This keeps build logs readable outside an interactive terminal.
_IS_TTY = sys.stdout.isatty()


def _progress(text, transient=True):
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 128 + "\r")
        sys.stdout.write(text if transient else text + "\n")
    else:
        sys.stdout.write(text.strip() + "\n")
    sys.stdout.flush()


def submit_ipfs_hash(
    hhash,
    enclave_name,
    protocol_version,
    network,
    template_version,
    docker_composer_hash,
):
    url = f"{BASE_URL}/api/addHash"
    payload = {
        "hash": hhash,
        "enclave_name": enclave_name,
        "protocol_version": protocol_version,
        "network": network,
        "template_version": template_version,
        "docker_composer_hash": docker_composer_hash,
    }
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return response.json()
    else:
        print("Error:", response.json())
        exit(1)


def check_ipfs_hash_status(hash):
    """The service's answer for the hash, or None while the service does not
    know the hash yet (404)."""
    url = f"{BASE_URL}/api/checkHash/{hash}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        return None
    print("Could not connect to certificate extraction service:", response.text)
    print("Please try again later")
    exit(1)


def main(
    enclave_name,
    protocol_version,
    network,
    template_version,
    ipfs_hash="",
    docker_composer_hash="",
    chain_registered=False,
    sleep=time.sleep,
    now=time.monotonic,
):
    if not ipfs_hash:
        return

    SPINNER_FRAMES = ["■", "□", "▪", "▫"]

    CHECK = "\033[92m✔\033[0m  "
    FAIL = "\033[91m✘\033[0m  "

    frame_index = 0
    print()
    if chain_registered:
        # The image is registered on chain; the service queues it from the
        # ImageRegistered event, with this publish's IPFS peer. The hash is
        # posted only when that row has not appeared within the grace period.
        deadline = now() + CHAIN_ROW_GRACE_SECONDS
        while True:
            check_response = check_ipfs_hash_status(ipfs_hash)
            if check_response is not None:
                break
            if now() >= deadline:
                _progress(
                    f"\tThe certificate service has not picked the registration up"
                    f" from the chain in {CHAIN_ROW_GRACE_SECONDS} s; submitting the hash",
                    transient=False,
                )
                submit_ipfs_hash(
                    ipfs_hash,
                    enclave_name,
                    protocol_version,
                    network,
                    template_version,
                    docker_composer_hash,
                )
                break
            frame_index = (frame_index + 1) % len(SPINNER_FRAMES)
            _progress(
                f"\t{SPINNER_FRAMES[frame_index]}  Waiting for the certificate service"
                f" to pick the registration up from the chain..."
            )
            sleep(POLL_SECONDS)
    else:
        submit_ipfs_hash(
            ipfs_hash,
            enclave_name,
            protocol_version,
            network,
            template_version,
            docker_composer_hash,
        )

    message = "Waiting for public key extraction to complete..."
    _progress(f"\t{SPINNER_FRAMES[frame_index]}  {message}")
    while True:
        check_response = check_ipfs_hash_status(ipfs_hash)
        if check_response is None:
            sleep(POLL_SECONDS)
            continue
        if "publicKey" in check_response:
            if check_response["publicKey"] == 0:
                frame_index = (frame_index + 1) % len(SPINNER_FRAMES)
                if check_response.get("status") == "running" or check_response.get('queuePosition') == "Running":
                    message = f"Public key extraction is running, {check_response.get('percent', 0)}% done..."
                else:
                    message = f"Waiting for public key extraction to start. Queue position: {check_response.get('queuePosition', 'Unknown')}"
                _progress(f"\t{SPINNER_FRAMES[frame_index]}  {message}")
                sleep(POLL_SECONDS)
            elif check_response["publicKey"] in (-1, "-1"):
                _progress(f"\t{FAIL}Public key extraction", transient=False)
                print("\t\tThe certificate extraction failed.")
                print(f"\t\tReason:      {check_response.get('reason', 'not reported by the service')}")
                print(f"\t\tIPFS hash:   {ipfs_hash}")
                print(f"\t\tEnclave:     {enclave_name}  (network: {network}, protocol: {protocol_version}, template: {template_version})")
                print("\t\tFix the cause (keep the image pinned, build with the current SDK) and publish again:")
                print("\t\ta new submission of the same hash runs the extraction again.")
                print()
                exit(1)
            else:
                _progress(f"\t{CHECK}Public key extraction", transient=False)
                esr_address = check_response.get("esrWalletAddress")
                if esr_address:
                    return {"publicKey": check_response["publicKey"],
                            "esrWalletAddress": esr_address}
                return check_response["publicKey"]
        else:
            print("\t\tThe Ethernity cloud certificate extraction service is unavailable at this time. Please try again later.", check_response)
            exit(1)
