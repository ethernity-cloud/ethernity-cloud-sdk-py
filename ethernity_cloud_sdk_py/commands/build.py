import os
import sys
import subprocess

from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config

config = Config(Path(".config.json").resolve())
config.load()


#print("Configuration loaded:", config.config)


def main():
    # Load environment variables from .env file
    version = config.read("VERSION")
    project_name = config.read("PROJECT_NAME")
    version += 1
    #print("Incrementing version to", version)

    config.write("VERSION", version)
    config.write("IPFS_HASH", "")
    service_type = config.read("SERVICE_TYPE")
    print(f"""\u276f\u276f Initializing build process
   Project name: {project_name}
   Version: {version}""")

    if service_type == "Nodenithy":
        #print("Adding prerequisites for Nodenithy...")
        script_path = Path(__file__).resolve().parent / "nodenithy" / "build.py"
        #print(f"Running script: {script_path}")
        try:
            subprocess.run(["python", str(script_path)], check=True)
            print(
                "Build script finished. You can now proceed to publish: ecld-publish."
            )
        except subprocess.CalledProcessError:
            print("Error running the build script.")
            exit(1)
    elif service_type == "Pynithy":
        #print("Adding prerequisites for Pynithy...")
        import ethernity_cloud_sdk_py.commands.pynithy.build as buildScript

        # script_path = Path(__file__).resolve().parent / "pynithy" / "build.py"
        #print(f"Running script: buildScript")
        # try:
        #     subprocess.run(["python", str(script_path)], check=True)
        #     print(
        #         "Build script finished. You can now proceed to publish: ecld-publish."
        #     )
        # except subprocess.CalledProcessError:
        #     print("Error running the build script.")
        #     exit(1)
        try:
            buildScript.main()
            print(
                """
Build process was successful! You can now proceed to publish by running:

    ecld-publish
"""
            )
        except Exception as e:
            print(f"Error running the build script: {e}")
            exit(1)
    else:
        print("Something went wrong")
        exit(1)


if __name__ == "__main__":
    main()
