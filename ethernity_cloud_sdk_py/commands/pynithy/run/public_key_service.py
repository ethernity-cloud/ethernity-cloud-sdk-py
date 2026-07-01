import requests
import time
import sys
BASE_URL = "https://publickey.ethernity.cloud"

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
    #print(f"response:{response}")
    if response.status_code == 200:
        return response.json()
    else:
        print("Error:", response.json())
        exit(1)


def check_ipfs_hash_status(hash):
    url = f"{BASE_URL}/api/checkHash/{hash}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
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
):
    if not ipfs_hash:
        return

    SPINNER_FRAMES = [
            "■",
            "□",
            "▪",
            "▫"
    ]

    CHECK = "\033[92m\u2714\033[0m  "
    FAIL = "\033[91m\u2718\033[0m  "

    # Submit IPFS Hash
    response = submit_ipfs_hash(
        ipfs_hash,
        enclave_name,
        protocol_version,
        network,
        template_version,
        docker_composer_hash,
    )
    #print("Recieved the following queueId:", response["queueId"])

    frame_index = 0
    
    print()
    message = f"Waiting for public key extraction to complete..."

    _progress(f"\t{SPINNER_FRAMES[frame_index]}  {message}")

    # Check IPFS Hash Status
    while True:
        check_response = check_ipfs_hash_status(ipfs_hash)
        if "publicKey" in check_response:
            if check_response["publicKey"] == 0:
                frame_index = (frame_index + 1) % len(SPINNER_FRAMES)
                if check_response.get('queuePosition') == "Running":
                    message = f"Public key extraction is running now. Waiting for completion..."
                else:
                    message = f"Waiting for public key extraction to start. Queue position: {check_response.get('queuePosition', 'Unknown')}"

                _progress(f"\t{SPINNER_FRAMES[frame_index]}  {message}")
                time.sleep(1)
            elif check_response["publicKey"] == "-1":
                _progress(f"\t{FAIL}Public key extraction", transient=False)
                print("\t\tThe certificate extraction process failed.")
                print(f"\t\tIPFS hash:   {ipfs_hash}")
                print(f"\t\tEnclave:     {enclave_name}  (network: {network}, protocol: {protocol_version}, template: {template_version})")
                print( "\t\tMake sure the enclave is built using the latest version of the SDK.")
                print(f"\t\tIf it is, the extraction service log for this hash holds the reason;")
                print(f"\t\tre-run the build or contact support with the IPFS hash above.")
                print()
                exit(1)
            else:
                _progress(f"\t{CHECK}Public key extraction", transient=False)
                return check_response["publicKey"]
        else:
            print("\t\tThe Ethernity cloud certificate extraction service is unavailable at this time. Please try again later.", check_response)
            exit(1)


