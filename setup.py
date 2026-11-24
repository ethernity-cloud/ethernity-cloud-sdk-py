from setuptools import setup, find_packages
from pathlib import Path

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()
setup(
    name="ethernity-cloud-sdk-py",
    version="2.0.0b1",
    url="https://github.com/ethernity-cloud/ethernity-cloud-sdk-py",
    author="Ethernity Cloud Team",
    author_email="contact@ethernity.cloud",
    description="Ethernity Cloud SDK Python",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(exclude=["tests", "tests.*"]),
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
        # The runner of the PoX 2.0 line: 2.0.0b1 up to below 3.0.0. A
        # requirement that names a prerelease lets pip install prereleases
        # for it.
        "ethernity-cloud-runner-py>=2.0.0b1,<3.0.0",
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
