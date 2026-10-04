
<p align="center">
  <a href="https://ethernity.cloud" title="Ethernity Cloud">
    <img src="https://ethernity.cloud/images/dark_gradient_logo.svg" alt="Ethernity Cloud logo" width="244" />
  </a>
</p>

<h3 align="center">The Python implementation of the Ethernity CLOUD SDK protocol</h3>

# Ethernity Cloud SDK PY

This project provides a set of tools and scripts to work with the Ethernity Cloud SDK in a python environment.

## Table of Contents

- [Pre-requisites](#pre-requisites)
- [Installation](#installation)
- [Usage](#usage)
  - [Scripts](#scripts)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## Pre-requisites
Linux:
- build-essentials
- Python 3.10+
- docker 
- docker-compose

Windows:
- Python 3.13
- Docker Desktop

## Installation

To install the package and its dependencies, run:

```sh
python -m venv venv

source venv/bin/activate # for MacOS and Linux
venv\Scripts\activate # for Windows

pip install ethernity-cloud-sdk-py
```

## Usage

After installation, you can use the provided scripts to build, publish, and initialize your project.

## Operating System compatibility
The sdk has been tested on the following operating systems:
- Windows 10
- linux (Ubuntu 20.04)

## Blockchain compatibility
- Bloxberg:
    - Testnet - tested and working. CAS-attested: the securelock is
      provisioned by the ethernity-cas validator set exactly as on mainnet.
      `ecld-publish` registers the securelock session on-chain in the
      SessionRegistry (`0xcb1F389b...`), resolves a CAS from the
      ValidatorRegistry (`0x0014aB10...`, or `ECLD_CAS_ADDR=host:port`) for
      the compose and the certificate harvest, and links the session to the
      published image. `ecld-build` signs the securelock `--production`.
      Harvesting the certificate needs SGX; the remote extraction service
      does not reach this CAS. `tests/e2e_testnet_cas.sh` runs the whole
      path -- init, build, publish, one task, and the CAS validators'
      verdict on it. The other testnets (Amoy, IoTeX, Sepolia, LiteForge)
      have no CAS deployment and self-sign from MR_ENCLAVE as before.
    - Mainnet - to be provided during the following updates
- Polyhon:
    - Amoy Testnet - to be provided during the following updates
    - Mainnet - to be provided during the following updates

### Scripts

- **Initialize**: To initialize the project, run:
  ```sh
  ecld-init
  ```
  at this step, all the initial configurations will be set up and the project will be ready to be built, published and run.

- **Build**: To build the project, run:
  ```sh
  ecld-build
  ```
    the project will be built and the docker repository output will be stored in the `registry/` directory. This is the stage where the backend functions are added to the secure images.

- **Publish**: To publish the project, run:
  ```sh
  ecld-publish
  ```
  Required after build, to build and integrate the secure certificates that will be used during executions and to register the project to the Ethernity Cloud Image Register.
  The developer fee the network pays for each task run with the image goes to
  the image's reward address: the publishing wallet, unless `REWARD_ADDRESS`
  in `.config.json` names another one. The image's owner can change it later
  with the registry's `changeImageRewardAddress`.

- **Test (local, no chain)**: To run your backend locally with the enclave's own
  executor — no SGX, no gas, instant — run:
  ```sh
  ecld-test 'hello("World")'
  ecld-test --file payload.py
  ecld-test --input data.json 'process(___etny_data_set___)'
  ```
  This validates the *call* (function names, arguments, result wiring) before a
  single on-chain request is paid for. If your dApp uses ESR (Enclave State
  Registry), state is emulated locally and **on by default**: `StateRegistry`
  get/commit, ownership/ACL, and `task_caller()` all work in-process against an
  in-memory registry, and persist between runs in `.ecld-esr-local.*.json`. The
  task caller defaults to your developer address; override it with `--caller`.
  `ecld-test serve` starts a local API so the runner's LOCAL mode drives your
  real integration end to end. Exit code 0 on `SUCCESS`, 1 otherwise.

- **Run (on the network)**: To submit a payload to the network and print the
  decrypted result, run:
  ```sh
  ecld-run 'hello("World")'
  ecld-run --file payload.py
  ecld-run --input data.json 'process(___etny_data_set___)'
  ecld-run --json 'esr_increment()'
  ```
  This is the network-side sibling of `ecld-test`: instead of executing locally
  it drives the runner end to end — encrypt → IPFS → on-chain request → wait for
  a node → download and decrypt the result. It **costs gas and needs a funded
  key**. Network and enclaves come from `.config.json` (`BLOCKCHAIN_NETWORK`,
  `PROJECT_NAME`, `TRUSTED_ZONE_IMAGE`), overridable with
  `--network`/`--securelock`/`--trustedzone`; the signing key is
  `ECLD_PRIVATE_KEY` (plaintext) or the encrypted `ENC_PRIVATE_KEY` unlocked with
  `ECLD_KEY_PASSWORD` (or an interactive prompt). Resources are tunable with
  `--task-price`/`--cpu`/`--memory`/`--storage`/`--bandwidth`/`--duration`/`--validators`.
  Exit code 0 on a `SUCCESS` task result, 1 otherwise. (`python src/ethernity_task.py`
  remains available as a scriptable template if you prefer driving the runner yourself.)
  `--unsafe` runs the network's `-unsafe` variant (see below).

- **The testnets and their unsafe networks**: `Bloxberg Testnet` (`BLOXBERG_TESTNET`) and
  `LitVM LiteForge` (`LITVM_LITEFORGE`) run your securelock in an enclave
  attested through that chain's Ethernity CAS validator set, on nodes whose
  platform the CAS can attest (DCAP). Each has an unsafe network beside it,
  `Bloxberg Testnet unsafe (no CAS)` (`BLOXBERG_TESTNET_UNSAFE`) and `LitVM
  LiteForge unsafe (no CAS)` (`LITVM_LITEFORGE_UNSAFE`): the same chain and
  contracts, for hardware SGX platforms the CAS cannot attest (EPID-only,
  SGX1). No CAS: enclaves are debug-signed and self-signed from their
  measurement, so a result proves which image ran but not that an enclave ran
  it. You choose an unsafe network by name, and the runner runs an `-unsafe`
  trustedzone on nothing else, so a dApp that runs without a CAS always says
  so in the network it names. A dApp publishes one securelock per network from
  two project directories with the same project name: the unsafe one is
  registered as `<project>-unsafe` and runs on the `etny-pynithy-testnet-unsafe`
  (`ecld-pynithy-litvm-testnet-unsafe`) trustedzone. Mainnet has no unsafe
  network.

- **Inspect (read-only)**: To read enclave and on-chain diagnostics — network,
  trustedzone/securelock registration, and ESR state — without spending gas, run:
  ```sh
  ecld-info
  ecld-info esr state <key>
  ```

## Usage

To use the SDK:
- after installation, run `ecld-init` to initialize the project
- in you workspace, you will find the `scr/serverless` directory, this contains a `backend.py` file. This file will be imported in the dApp images to provide the backend functions for calling from the frontend of your application, eg.:
```py
def hello(msg='World'):
    return "Hello "+msg
```
From your py application, using the ethernity cloud runner library, you will be calling the function as seen in the below example, where we pass `hello("World")` to be executed on the backend which will run in the Blockchain:
```py
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from ethernity_cloud_runner_py.runner import EthernityCloudRunner  # type: ignore


def execute_task() -> None:
    ipfs_address = "https://ipfs.ethernity.cloud/api/v0"

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
        os.getenv("TRUSTED_ZONE_IMAGE", ""),
    )


if __name__ == "__main__":
    execute_task()

```
- you are able to define the functions needed to be used in the backend, while making sure that the function that is script is compilable and that it exports the function that will be called from the frontend, in the above example, the `hello` function.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

This project is licensed under the AGPL-3.0 License. See the LICENSE file for details.
