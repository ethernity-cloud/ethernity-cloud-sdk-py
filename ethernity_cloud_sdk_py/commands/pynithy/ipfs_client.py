import requests  # type: ignore
import argparse
import os, sys, time
import json
from tqdm import tqdm
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)

RETRY_COUNT = 10


class IPFSClient:

    SPINNER_FRAMES = [
        '⠋', '⠙', '⠹', '⠸', '⠼',
        '⠴', '⠦', '⠧', '⠇', '⠏'
    ]

    CHECK = "\033[92m\u2714\033[0m  "
    FAIL = "\033[91m\u2718\033[0m  "

    def __init__(
        self, ipfs_endpoint, token = None
    ) -> None:
        self.api_url = ipfs_endpoint
        self.add_url = f"{self.api_url}/api/v0/add"
        self.frame_index = 0
        self.headers = {}
        self.folder_path = ""
        self.files = []
        self.total_size = 0

        if token:
            self.headers = {"Authorization": token}

    def upload_file(self, file_path: str) -> None:
        add_url = f"{self.api_url}/api/v0/add"

        with open(file_path, "rb") as file:
            files = {"file": file}
            response = requests.post(add_url, files=files, headers=self.headers)

        if response.status_code == 200:
            try:
                response_data = response.json()
                ipfs_hash = response_data["Hash"]
                return ipfs_hash
            except Exception as e:
                print(f"Failed to upload to IPFS. Error: {e}")
                return None
        else:
            print(f"Failed to upload to IPFS. Status code: {response.status_code}")
            print(response.text)
            return None

    def update_progress(self, monitor):
        mb_total = self.total_size / (1024 * 1024)
        mb_read = monitor.bytes_read / (1024 * 1024)

        # Convert the currently read bytes into whole MB for updating
        current_mb = int(mb_read)  # integer MB boundary

        if not hasattr(self, "_last_shown_mb"):
            self._last_shown_mb = -1

        # Update output only when a new MB has been fully read
        if current_mb > self._last_shown_mb:
            # Update the spinner frame
            self.frame_index = (self.frame_index + 1) % len(self.SPINNER_FRAMES)
            self._last_shown_mb = current_mb
            sys.stdout.write(
                f"\r\t{self.SPINNER_FRAMES[self.frame_index]}  Uploading and pinning enclave to IPFS... {current_mb}MB/{int(mb_total)}MB"
            )
            sys.stdout.flush()

    def upload_dir(self, dir_path):
        # Ensure directory path is absolute and valid for Windows
        dir_path = os.path.abspath(dir_path)

        # Gather all files in the directory as a list of file paths
        self.files = []
        for root, dirs, files in os.walk(dir_path):
            for filename in files:
                filepath = os.path.join(root, filename)
                self.files.append(filepath)

        # Compute total size of all files
        self.total_size = sum(os.path.getsize(filepath) for filepath in self.files)

        # Create the MultipartEncoder fields
        # Use relative paths as keys and values as per IPFS HTTP API expectations
        fields = {
            os.path.relpath(filepath, dir_path).replace("\\", "/"): (
                os.path.relpath(filepath, dir_path).replace("\\", "/"),
                open(filepath, "rb")
            )
            for filepath in self.files
        }

        encoder = MultipartEncoder(fields=fields)
        monitor = MultipartEncoderMonitor(encoder, self.update_progress)

        response = requests.post(
            self.add_url + "?quieter=true&stream-channels=true&wrap-with-directory=true&progress=false&timeout=5m",
            data=monitor,
            stream=True,
            headers={
                **self.headers,
                "Content-Type": monitor.content_type,
                "Content-Length": str(self.total_size),
                "Expect": "100-continue",
            },
        )

        # Once done, move to the next line to avoid overwriting the last spinner line
        #sys.stdout.write("\n")

        if response.status_code == 200:
            try:
                # IPFS add endpoint often returns newline-delimited JSON. Convert it into valid JSON.
                # Example: Each line is a JSON object, so we join them with commas into an array.
                response_data = json.loads("[" + response.text.replace("\n", ",")[:-1] + "]")
                for file_info in response_data:
                    # An empty "Name" often indicates the root hash of the added directory
                    if file_info["Name"] == "":
                        ipfs_hash = file_info["Hash"]
                        sys.stdout.write("\r" + f"\t{self.CHECK}Uploading and pinning enclave to IPFS")
                        return ipfs_hash
            except Exception as e:
                sys.stdout.write("\r" + f"\t{self.FAIL}Uploading and pinning enclave to IPFS")
                print(f"Failed to upload to IPFS. Error: {e}")
                return False
        else:
            sys.stdout.write("\r" + f"\t{self.FAIL}Uploading and pinning enclave to IPFS")
            print(f"Failed to upload to IPFS. Status code: {response.status_code}")
            #print(response.text)
            return False

    def download_file(
        self, ipfs_hash: str, download_path: str, attempt: int = 0
    ) -> None:
        gateway_url = f"https://ipfs.io/ipfs/{ipfs_hash}"
        response = requests.get(url=gateway_url, timeout=60, headers=self.headers)

        if response.status_code == 200:
            with open(download_path, "wb") as file:
                file.write(response.content)
            print(f"File downloaded successfully to {download_path}")
        else:
            print(
                f"Failed to download from IPFS. Attempt {attempt}. Status code: {response.status_code}. Response text: {response.text}.\n{'Trying again...' if attempt < 6 else ''}"
            )
            if attempt < 6:
                self.download_file(ipfs_hash, download_path, attempt + 1)

    def get_file_content(self, ipfs_hash: str, attempt: int = 0) -> None:
        url = self.api_url
        gateway_url = f"{url}/api/v0/cat?arg={ipfs_hash}"
        response = requests.post(url=gateway_url, timeout=60, headers=self.headers)

        if response.status_code == 200:
            # TODO: use a get encoding function to determine the encoding
            return response.content.decode("utf-8")
        else:
            print(
                f"Failed to get content from IPFS. Attempt {attempt}. Status code: {response.status_code}. Response text: {response.text}.\n{'Trying again...' if attempt < 6 else ''}"
            )
            if attempt < 6:
                self.get_file_content(ipfs_hash, attempt + 1)

        return None
    
    def upload(self, path: str) -> str:
        if os.path.isfile(path):
            # It's a single file
            return self.upload_file(path)
        elif os.path.isdir(path):
            # It's a directory
            return self.upload_dir(path)
        else:
            print(f"Path {path} is neither a file nor a directory.")
            return None