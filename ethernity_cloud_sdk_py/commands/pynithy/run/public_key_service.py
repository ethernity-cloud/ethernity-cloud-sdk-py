import requests
import time
BASE_URL = "https://publickey.ethernity.cloud"


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
        print("Error:", response.json())
        exit(1)


def main(
    enclave_name,
    protocol_version,
    network,
    template_version,
    hhash="",
    docker_composer_hash="",
):
    print("Enclave Name:", enclave_name)
    print("Protocol Version:", protocol_version)
    print("Network:", network)
    print("Template Version:", template_version)

    if not hhash:
        return

    print()
    print("IPFS Hash:", hhash)
    print("Docker Composer Hash:", docker_composer_hash)
    print()

    # Submit IPFS Hash
    response = submit_ipfs_hash(
        hhash,
        enclave_name,
        protocol_version,
        network,
        template_version,
        docker_composer_hash,
    )
    print("Recieved the following queueId:", response["queueId"])

    # Check IPFS Hash Status
    while True:
        check_response = check_ipfs_hash_status(hhash)
        if "publicKey" in check_response:
            if check_response["publicKey"] == 0:
                print(
                    f"Public key not available yet. Queue position: {check_response.get('queuePosition', 'Unknown')}"
                )
            elif check_response["publicKey"] == -1:
                print("Hash is not derived from Eternity Cloud SDK.")
                exit(1)
            else:
                #print("Public Key:", check_response["publicKey"])
                # save public key to file
                with open("PUBLIC_KEY.txt", "w") as f:
                    f.write(check_response["publicKey"])
                break
        else:
            print("Unexpected response:", check_response)
            exit(1)

        time.sleep(10)  # Wait for 10 seconds before checking again


