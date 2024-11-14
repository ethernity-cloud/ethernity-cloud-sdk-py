import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv


def main():
    # Load environment variables from .env file
    current_dir = os.getcwd()
    dotenv_path = os.path.join(current_dir, ".env")
    print(f"Looking for .env file at: {dotenv_path}")
    load_dotenv(dotenv_path=dotenv_path)
    service_type = os.getenv("SERVICE_TYPE")
    print(f"Service type: {service_type}")
    if service_type == "Nodenithy":
        print("Adding prerequisites for Nodenithy...")
        script_path = Path(__file__).resolve().parent / "nodenithy" / "build.py"
        print(f"Running script: {script_path}")
        try:
            subprocess.run(["python", str(script_path)], check=True)
            print(
                "Build script finished. You can now proceed to publish: ecld-publish."
            )
        except subprocess.CalledProcessError:
            print("Error running the build script.")
            sys.exit(1)
    elif service_type == "Pynithy":
        print("Adding prerequisites for Pynithy...")
        import ethernity_cloud_sdk_py.commands.pynithy.build as buildScript

        # script_path = Path(__file__).resolve().parent / "pynithy" / "build.py"
        print(f"Running script: buildScript")
        # try:
        #     subprocess.run(["python", str(script_path)], check=True)
        #     print(
        #         "Build script finished. You can now proceed to publish: ecld-publish."
        #     )
        # except subprocess.CalledProcessError:
        #     print("Error running the build script.")
        #     sys.exit(1)
        try:
            buildScript.main()
            print(
                "Build script finished. You can now proceed to publish: ecld-publish."
            )
        except Exception as e:
            print(f"Error running the build script: {e}")
            sys.exit(1)
    else:
        print("Something went wrong")
        sys.exit(1)


if __name__ == "__main__":
    main()
