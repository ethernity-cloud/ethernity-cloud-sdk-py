import requests  # type: ignore
import argparse
import os, sys, time
import json
import functools
import itertools
import random
from tqdm import tqdm
from requests.exceptions import RequestException, SSLError
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)

RETRY_COUNT = 10  # Or keep configurable elsewhere

# IPFSClient.pin: how many times `pin/add` is asked for one CID, and how long
# the client waits before it asks again.
PIN_ATTEMPTS = 5
PIN_RETRY_SECONDS = 10
# How long a request of the pin may take to connect, and its `pin/ls` to
# answer.
PIN_REQUEST_SECONDS = 30
# The longest silence in an answer of `pin/add`. Asked with progress=true, the
# API writes a line every half second while it fetches the blocks.
PIN_SILENCE_SECONDS = 120
# How many bytes one read of an answer of `pin/add` asks for. A read of one
# returns when a byte arrives, whatever the framing of the answer; a read of
# more waits until that many have arrived, or the body or the chunk they are
# in has ended.
PIN_READ_BYTES = 1

def retry_on_failure(max_attempts=RETRY_COUNT, initial_delay=10, backoff_factor=2, jitter=0.1):
    def decorator_retry(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_attempts):
                try:
                    # Optional: reset progress tracking if present
                    if args and hasattr(args[0], "_last_shown_mb"):
                        args[0]._last_shown_mb = -1
                        args[0].frame_index = 0

                    return func(*args, **kwargs)

                except (RequestException, SSLError, Exception) as e:
                    print(f"[Retry {attempt + 1}/{max_attempts}] {func.__name__} failed due to: {type(e).__name__} - {e}")

                    if attempt < max_attempts - 1:
                        time.sleep(delay + random.uniform(0, jitter))
                        delay *= backoff_factor  # Exponential backoff
            print("Max retry attempts reached. Operation failed.")
            return None
        return wrapper
    return decorator_retry


def _json_object(text):
    """The JSON object `text` holds, or {} when it holds anything else."""
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


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
        self._step = "Uploading and pinning enclave to IPFS"

        if token:
            self.headers = {"Authorization": token}
    @retry_on_failure()
    def upload_file(self, file_path: str, only_hash: bool = False) -> None:
        """Add a file and return its CID. With only_hash the node computes
        the same CID and neither stores nor announces anything."""
        add_url = f"{self.api_url}/api/v0/add" + ("?only-hash=true" if only_hash else "")

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
                f"\r\t{self.SPINNER_FRAMES[self.frame_index]}  {self._step}... {current_mb}MB/{int(mb_total)}MB"
            )
            sys.stdout.flush()

    def upload_dir(self, dir_path, attempts=3, only_hash=False):
        """Add a directory tree and return its root CID. With only_hash the
        node computes the same CID and neither stores nor announces anything.

        The enclave image is ~1.2 GB and the IPFS API answers the add only
        after the last byte, so a failure shows up after minutes of transfer:
        as a non-200 response, or as the RequestException requests raises for
        a connection dropped mid-upload (SSLError, ConnectionError). Each
        attempt re-sends the whole tree; `attempts` bounds it.
        """
        self._step = "Hashing enclave for IPFS" if only_hash else "Uploading and pinning enclave to IPFS"
        for attempt in range(1, attempts + 1):
            try:
                ipfs_hash = self._upload_dir_once(dir_path, only_hash)
            except RequestException as e:
                sys.stdout.write("\r" + f"\t{self.FAIL}{self._step}\n")
                print(f"\tthe upload connection failed: {type(e).__name__}: {e}")
                ipfs_hash = False
            if ipfs_hash:
                return ipfs_hash
            if attempt < attempts:
                print(f"\tretrying the upload ({attempt}/{attempts} failed)")
        return False

    def _upload_dir_once(self, dir_path, only_hash=False):

        self._last_shown_mb = -1
        self.frame_index = 0

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
        handles = [open(filepath, "rb") for filepath in self.files]
        try:
            fields = {
                os.path.relpath(filepath, dir_path).replace("\\", "/"): (
                    os.path.relpath(filepath, dir_path).replace("\\", "/"),
                    handle
                )
                for filepath, handle in zip(self.files, handles)
            }

            encoder = MultipartEncoder(fields=fields)
            monitor = MultipartEncoderMonitor(encoder, self.update_progress)

            # `timeout` is the IPFS API's own bound on the add; 60m covers a
            # 1.2 GB upload over an ordinary uplink.
            response = requests.post(
                self.add_url + "?quieter=true&stream-channels=true&wrap-with-directory=true&progress=false&timeout=60m"
                + ("&only-hash=true" if only_hash else ""),
                data=monitor,
                stream=True,
                headers={
                    **self.headers,
                    "Content-Type": monitor.content_type,
                    "Content-Length": str(self.total_size),
                    "Expect": "100-continue",
                },
            )
        finally:
            for handle in handles:
                handle.close()

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
                        sys.stdout.write("\r" + f"\t{self.CHECK}{self._step}")
                        return ipfs_hash
                # A 200 with no root entry: the API closed the add before the
                # wrapping directory was written.
                sys.stdout.write("\r" + f"\t{self.FAIL}{self._step}")
                print(f"Failed to upload to IPFS: the IPFS API returned no root hash "
                      f"({len(response.text)} bytes of response)")
                return False
            except Exception as e:
                sys.stdout.write("\r" + f"\t{self.FAIL}{self._step}")
                print(f"Failed to upload to IPFS. Error: {e}")
                return False
        else:
            sys.stdout.write("\r" + f"\t{self.FAIL}{self._step}")
            print(f"Failed to upload to IPFS. Status code: {response.status_code}")
            #print(response.text)
            return False

    def pin(self, cid: str, timeout_secs: int = 3600) -> bool:
        """Pin `cid` on this API (`pin/add`), which fetches the blocks it does
        not hold from the peers that provide them. The pin is asked with
        progress=true: the API answers with a stream of lines, one every half
        second while it fetches and one that names the CID among its Pins
        once the pin is complete. An attempt that reads no such line (the
        answer ends, breaks, stays silent for PIN_SILENCE_SECONDS, or is an
        error) is followed by `pin/ls`, which tells whether the API holds the
        pin; when it does not, `pin/add` is asked again after
        PIN_RETRY_SECONDS, PIN_ATTEMPTS times at most, and no request starts
        once `timeout_secs` have passed. The `pin/add` being read then ends
        later: at the next byte of an answer still arriving, and up to
        PIN_SILENCE_SECONDS after the last byte of one that has fallen
        silent. A 401 or 403, the API refusing the token, ends the pin at
        once. Returns True when the pin is complete, having printed the
        number of attempts when there was more than one; otherwise prints
        what the last attempt ended with and returns False."""
        deadline = time.monotonic() + timeout_secs
        for attempt in range(1, PIN_ATTEMPTS + 1):
            failure, refused = self._pin_add(cid, deadline)
            if failure is None or (not refused and self._pinned(cid, deadline)):
                if attempt > 1:
                    print(f"\tthe pin took {attempt} attempts")
                return True
            if refused or attempt == PIN_ATTEMPTS or deadline - time.monotonic() <= PIN_RETRY_SECONDS:
                break
            time.sleep(PIN_RETRY_SECONDS)
        tried = f" in {attempt} attempts" if attempt > 1 else ""
        print(f"\tFailed to pin {cid} on {self.api_url}{tried}: {failure}")
        return False

    def _pin_add(self, cid, deadline):
        """One `pin/add` of `cid`, asked without compression and read
        PIN_READ_BYTES at a read until the line that reports the pin (in an
        answer that is no 200, until its first 300 characters), the answer's
        end or the first read that returns after `deadline`. Returns
        (failure, refused): failure is None when the pin is reported and says
        what the attempt ended with otherwise; refused is True when that is a
        401 or 403."""
        out_of_time = "the pin was not complete when its time ran out"
        left = deadline - time.monotonic()
        if left <= 0:
            return out_of_time, False
        try:
            response = requests.post(
                f"{self.api_url}/api/v0/pin/add?arg={cid}&recursive=true&progress=true",
                headers={**self.headers, "Accept-Encoding": "identity"}, stream=True,
                timeout=(min(PIN_REQUEST_SECONDS, left), min(PIN_SILENCE_SECONDS, left)))
            try:
                if response.status_code != 200:
                    # The first 300 characters of a body in UTF-8 are within
                    # its first 1200 bytes.
                    body = b""
                    for chunk in response.iter_content(PIN_READ_BYTES):
                        body += chunk
                        if len(body) >= 1200 or time.monotonic() >= deadline:
                            break
                    return (f"status {response.status_code}\n\t{body.decode('utf-8', 'replace')[:300]}",
                            response.status_code in (401, 403))
                pending = b""
                # The line feed after the answer's end makes a line of what
                # the answer left without one.
                for chunk in itertools.chain(response.iter_content(PIN_READ_BYTES), [b"\n"]):
                    *lines, pending = (pending + chunk).split(b"\n")
                    for answer in map(_json_object, lines):
                        pins = answer.get("Pins")
                        if isinstance(pins, list) and cid in pins:
                            return None, False
                        if answer.get("Type") == "error":
                            return str(answer.get("Message")), False
                    if time.monotonic() >= deadline:
                        return out_of_time, False
                return "the answer ended without the pin", False
            finally:
                response.close()
        except RequestException as e:
            return f"{type(e).__name__}: {e}", False

    def _pinned(self, cid, deadline):
        """Whether `pin/ls` lists `cid` among the recursive pins of this API.
        False also when the API does not answer the question, and when no
        time is left before `deadline` to ask it."""
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        try:
            response = requests.post(
                f"{self.api_url}/api/v0/pin/ls?arg={cid}&type=recursive",
                headers=self.headers, timeout=min(PIN_REQUEST_SECONDS, left))
        except RequestException:
            return False
        keys = _json_object(response.text).get("Keys")
        return response.status_code == 200 and isinstance(keys, dict) and cid in keys

    def upload(self, path: str, only_hash: bool = False) -> str:
        if os.path.isfile(path):
            # It's a single file
            return self.upload_file(path, only_hash=only_hash)
        elif os.path.isdir(path):
            # It's a directory
            return self.upload_dir(path, only_hash=only_hash)
        else:
            print(f"Path {path} is neither a file nor a directory.")
            return None