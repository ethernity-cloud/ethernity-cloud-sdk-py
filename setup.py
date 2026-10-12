from setuptools import setup, find_packages
from pathlib import Path

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()
setup(
    name="ethernity-cloud-sdk-py",
    version="0.9.13",
    url="https://github.com/ethernity-cloud/ethernity-cloud-sdk-py",
    author="Ethernity Cloud Team",
    author_email="contact@ethernity.cloud",
    description="Ethernity Cloud SDK Python",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "ethernity_cloud_sdk_py.templates": ["src/**/*"],
    },
    install_requires=[
        "requests",
        "python-dotenv",
        "tqdm",
        "pyopenssl",
        "requests_toolbelt",
        "cryptography",
        "typing-extensions",
        # 0.5.8: the LitVM ECImageRegistryV3 0xAf8Ce291 and the bootnode's
        # payload intake serving the LitVM networks, as this SDK's LitVM
        # network names them.
        "ethernity-cloud-runner-py>=0.5.8",
        "pyyaml",
        # Add other dependencies here
    ],
    entry_points={
        "console_scripts": [
            "ecld-init=ethernity_cloud_sdk_py.cli:main_init",
            "ecld-build=ethernity_cloud_sdk_py.cli:main_build",
            "ecld-publish=ethernity_cloud_sdk_py.cli:main_publish",
            "ecld-test=ethernity_cloud_sdk_py.cli:main_test",
            "ecld-info=ethernity_cloud_sdk_py.cli:main_info",
            "ecld-run=ethernity_cloud_sdk_py.cli:main_run",
        ],
    },
)
