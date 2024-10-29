
# Ethernity Cloud SDK PY

This project provides a set of tools and scripts to work with the Ethernity Cloud SDK in a JavaScript environment.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
  - [Scripts](#scripts)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## Installation

To install the package and its dependencies, run:

```sh
python -m venv venv

source venv/bin/activate # for MacOS and Linux
venv\Scripts\activate # for Windows

pip install ethernity-cloud-sdk-py-dev
```

## Usage

After installation, you can use the provided scripts to build, publish, and initialize your project.

## Pre-requisites
The sdk requires the following to be installed on your system:
- python 3.10+
- docker (daemon running in the background for build and publish scripts)


## Operating System compatibility
The sdk has been tested on the following operating systems:
- Windows 10

## Blockchain compatibility
- Bloxberg:
    - Testnet - tested and working
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

- **Run**: To run the project, run:
  ```sh
  python src/ethernity_task.py
  ```
  command to start the demo application and test the integration.

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

```
- you are able to define the functions needed to be used in the backend, while making sure that the function that is script is compilable and that it exports the function that will be called from the frontend, in the above example, the `hello` function.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

This project is licensed under the AGPL-3.0 License. See the LICENSE file for details.
