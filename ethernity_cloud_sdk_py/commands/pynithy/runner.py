import os
import sys
import subprocess
import re
import json
import shutil
import requests
from pathlib import Path
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa


def write_env(key, value):
    env_file = os.path.join(current_dir, ".env")
    if not os.path.exists(env_file):
        with open(env_file, "w") as f:
            f.write(f"{key}={value}\n")
        return

    updated = False
    with open(env_file, "r") as f:
        lines = f.readlines()

    with open(env_file, "w") as f:
        for line in lines:
            if line.startswith(f"{key}="):
                f.write(f"{key}={value}\n")
                updated = True
            else:
                f.write(line)
        if not updated:
            f.write(f"{key}={value}\n")


def prompt(question, default_value=None):
    """
    Prompt user for input with an optional default value.
    """
    if default_value:
        question = f"{question} (default value: {default_value}) "
    else:
        question = f"{question} "
    user_input = input(question).strip()
    if not user_input and default_value is not None:
        return default_value
    return user_input


def prompt_options(message, options, default_option):
    while True:
        answer = input(message).strip().lower()
        if not answer:
            print(f"No option selected. Defaulting to {default_option}.")
            return default_option
        elif answer in options:
            return answer
        else:
            print(
                f'Invalid option "{answer}". Please enter one of: {", ".join(options)}.'
            )


def run_docker_command(service):
    command = f"docker-compose run -e SCONE_LOG=INFO -e SCONE_HASH=1 {service}"
    try:
        output = (
            subprocess.check_output(
                command, shell=True, cwd=run_dir, stderr=subprocess.STDOUT
            )
            .decode()
            .strip()
        )
        print(f"Output of {command}: {output}")
        # Filter the output
        filtered_output = "\n".join(
            line
            for line in output.split("\n")
            if not re.search(
                r"Creating|Created|Pulling|latest|Digest|Starting|Started", line
            )
        )
        return filtered_output
    except subprocess.CalledProcessError as e:
        print(f"Error running command '{command}': {e.output.decode()}")
        return ""


def process_yaml_template(template_file, output_file, replacements):
    if not os.path.exists(template_file):
        print(f"Error: Template file {template_file} not found!")
        sys.exit(1)
    with open(template_file, "r") as f:
        content = f.read()
    for key, value in replacements.items():
        content = content.replace(f"__{key}__", value)
    with open(output_file, "w") as f:
        f.write(content)
    # Check for remaining placeholders
    remaining_placeholders = re.findall(r"__.*?__", content)
    if remaining_placeholders:
        print("Remaining placeholders:", ", ".join(remaining_placeholders))
    else:
        print("No placeholders found.")


def main():
    load_dotenv()
    if not os.path.exists(".env"):
        print("Error: .env file not found")
        sys.exit(1)

    global current_dir, run_dir
    current_dir = os.getcwd()
    print(f"currentDir: {current_dir}")
    run_dir = Path(__file__).resolve().parent / "run"
    os.chdir(run_dir)
    # print("run_dir: ", run_dir)
    registry_path = os.path.join(current_dir, "registry")
    os.environ["REGISTRY_PATH"] = registry_path

    templateName = os.getenv("TRUSTED_ZONE_IMAGE", "etny-pynithy-testnet")
    isMainnet = False if "testnet" in templateName.lower() else True

    # Backup and restore docker-compose templates
    backup_files = ["docker-compose.yml.tmpl", "docker-compose-final.yml.tmpl"]
    for file in backup_files:
        if not os.path.exists(file):
            print(f"Error: {file} not found!")
            continue
        shutil.copyfile(file, file.replace(".tmpl", ""))
    print()
    # Run docker commands to get MRENCLAVE values
    mrenclave_securelock = run_docker_command("etny-securelock")
    print(f"MRENCLAVE_SECURELOCK: {mrenclave_securelock}")

    write_env("MRENCLAVE_SECURELOCK", mrenclave_securelock)

    # Process YAML template for etny-securelock
    ENCLAVE_NAME_SECURELOCK = os.getenv("ENCLAVE_NAME_SECURELOCK", "")
    print(f"\nENCLAVE_NAME_SECURELOCK: {ENCLAVE_NAME_SECURELOCK}")

    envPredecessor = os.getenv("PREDECESSOR_HASH_SECURELOCK", "EMPTY")
    PREDECESSOR_HASH_SECURELOCK = "EMPTY"
    PREDECESSOR_PROJECT_NAME = "EMPTY"
    PREDECESSOR_VERSION = "EMPTY"
    if envPredecessor != "EMPTY":
        PREDECESSOR_HASH_SECURELOCK = envPredecessor.split("$$$%$")[0]
        PREDECESSOR_PROJECT_NAME = envPredecessor.split("$$$%$")[1]
        PREDECESSOR_VERSION = envPredecessor.split("$$$%$")[2]

    print(f"PREDECESSOR_HASH_SECURELOCK: {PREDECESSOR_HASH_SECURELOCK}")
    print(f"PREDECESSOR_PROJECT_NAME: {PREDECESSOR_PROJECT_NAME}")
    print(f"PREDECESSOR_VERSION: {PREDECESSOR_VERSION}")

    if (
        PREDECESSOR_HASH_SECURELOCK != "EMPTY"
        and PREDECESSOR_PROJECT_NAME != os.getenv("PROJECT_NAME")
        and PREDECESSOR_VERSION != os.getenv("VERSION")
    ):
        PREDECESSOR_HASH_SECURELOCK = "EMPTY"

    replacements_securelock = {
        "PREDECESSOR": (
            f"# predecessor: {envPredecessor}"
            if envPredecessor == "EMPTY"
            else f"predecessor: {envPredecessor}"
        ),
        "MRENCLAVE": mrenclave_securelock,
        "ENCLAVE_NAME": ENCLAVE_NAME_SECURELOCK,
    }

    process_yaml_template(
        "etny-securelock-test.yaml.tpl",
        "etny-securelock-test.yaml",
        replacements_securelock,
    )
    print()
    # Generate certificates if needed
    key_pem_path = "key.pem"
    cert_pem_path = "cert.pem"
    if (
        envPredecessor != "EMPTY"
        and os.path.exists(key_pem_path)
        and os.path.exists(cert_pem_path)
    ):
        print("Skipping key pair generation and certificate creation.")
        print("Using existing key.pem and cert.pem files.")
    else:
        print("# Generating cert.pem and key.pem files")

        # Generate a key pair
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )

        # Get the public key
        public_key = private_key.public_key()

        # Get ENCLAVE_NAME_SECURELOCK from environment variable or default value
        organization_name = os.getenv(
            "ENCLAVE_NAME_SECURELOCK", "Internet Widgits Pty Ltd"
        )

        # Build subject and issuer names (self-signed certificate)
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "AU"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Some-State"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization_name),
            ]
        )

        # Set validity period (not before one year ago, not after two years from now)
        valid_from = datetime.utcnow() - timedelta(days=365)
        valid_to = valid_from + timedelta(days=3 * 365)  # Valid for 3 years total

        # Serial number (use 1 for consistency)
        serial_number = 1

        # Build the certificate
        builder = x509.CertificateBuilder()
        builder = builder.subject_name(subject)
        builder = builder.issuer_name(issuer)
        builder = builder.public_key(public_key)
        builder = builder.serial_number(serial_number)
        builder = builder.not_valid_before(valid_from)
        builder = builder.not_valid_after(valid_to)

        # Add extensions
        # 1. Subject Key Identifier
        builder = builder.add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False
        )

        # 2. Authority Key Identifier
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key),
            critical=False,
        )

        # 3. Basic Constraints (mark as CA)
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        )

        # Self-sign the certificate
        certificate = builder.sign(
            private_key=private_key,
            algorithm=hashes.SHA256(),
        )

        # Serialize private key to PEM format (PKCS8)
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # Serialize certificate to PEM format
        certificate_pem = certificate.public_bytes(
            encoding=serialization.Encoding.PEM,
        )

        # Write private key and certificate to files
        with open("key.pem", "wb") as f:
            f.write(private_key_pem)

        with open("cert.pem", "wb") as f:
            f.write(certificate_pem)

        print("# Generated cert.pem and key.pem files")

        # Read certificates and data
        with open(cert_pem_path, "rb") as f:
            cert_data = f.read()
        with open(key_pem_path, "rb") as f:
            key_data = f.read()
        with open("etny-securelock-test.yaml", "rb") as f:
            yaml_data = f.read()

        # Set up the request headers
        headers = {"Content-Type": "application/octet-stream"}
        print()
        # Perform the HTTPS POST request
        try:
            # Create a session to manage certificates and SSL settings
            session = requests.Session()
            session.verify = False  # Equivalent to rejectUnauthorized: false
            session.cert = ("cert.pem", "key.pem")  # Provide the client cert and key

            # Perform the POST request
            response = session.post(
                "https://scone-cas.cf:8081/session", data=yaml_data, headers=headers
            )
            # response.raise_for_status()  # Raise an exception for HTTP errors
            # print(f"Response status code: {response.status_code}")
            # print(f"Response text: {response.text}")
            # Write the response data to 'predecessor.json'
            with open("predecessor.json", "w", encoding="utf-8") as f:
                json.dump(response.json(), f, indent=2)
            print("# Updated session file for securelock and saved to predecessor.json")

            response_data = response.json()
            pred = response_data.get("hash", "EMPTY")
            project_name = os.getenv("PROJECT_NAME")
            version = os.getenv("VERSION")

            if pred != "EMPTY":
                predecessor_hash_securelock = (
                    f"{pred}$$$%${project_name}$$$%${version}" or "EMPTY"
                )
                write_env("PREDECESSOR_HASH_SECURELOCK", predecessor_hash_securelock)
                os.environ["PREDECESSOR_HASH_SECURELOCK"] = predecessor_hash_securelock
            else:
                predecessor_hash_securelock = "EMPTY"
                write_env("PREDECESSOR_HASH_SECURELOCK", predecessor_hash_securelock)
                os.environ["PREDECESSOR_HASH_SECURELOCK"] = predecessor_hash_securelock

            if predecessor_hash_securelock == "EMPTY":
                print("Error: Could not update session file for securelock")
                print(
                    "Please change the name/version of your project (using ecld-init or by editing .env file) and run the scripts again. Exiting."
                )
                sys.exit(1)

            print()
            print("Scone CAS registration successful.")
            print()

        except requests.RequestException as error:
            print("Scone CAS error:", error)
            print("Error: Could not update session file for securelock")
            print(
                "Please change the name/version of your project (using ecld-init or by editing .env file) and run the scripts again. Exiting."
            )
            sys.exit(1)

    ENCLAVE_NAME_TRUSTEDZONE = "etny-pynithy-trustedzone-v3-testnet-0.1.12"
    if isMainnet:
        ENCLAVE_NAME_TRUSTEDZONE = "ecld-pynithy-trustedzone-v3-3.0.0"
    print()
    # Update docker-compose files
    print("# Update docker-compose files")
    files = ["docker-compose.yml", "docker-compose-final.yml"]
    for file in files:
        if not os.path.exists(file):
            print(f"Error: {file} not found!")
            continue
        print(f"Processing {file}")
        with open(file, "r") as f:
            content = f.read()
        content = content.replace(
            "__ENCLAVE_NAME_SECURELOCK__", ENCLAVE_NAME_SECURELOCK
        ).replace("__ENCLAVE_NAME_TRUSTEDZONE__", ENCLAVE_NAME_TRUSTEDZONE)
        with open(file, "w") as f:
            f.write(content)
        remaining_placeholders = re.findall(r"__.*?__", content)
        if remaining_placeholders:
            print(
                f"Placeholders still found in {file}: {', '.join(remaining_placeholders)}"
            )
        else:
            print(f"Ok, No placeholders found in {file}")

    print()

    if os.path.exists("certificate.securelock.crt"):
        os.remove("certificate.securelock.crt")

    print("Fetching PUBLIC_KEY_SECURELOCK")
    PUBLIC_KEY_SECURELOCK_RES = ""
    try:
        output = (
            subprocess.check_output(
                "docker-compose run etny-securelock",
                shell=True,
                cwd=run_dir,
                stderr=subprocess.STDOUT,
            )
            .decode()
            .strip()
        )
        print("Output of docker-compose run etny-securelock:")
        lines = output.split("\n")
        publicKeyLine = next((line for line in lines if "PUBLIC_KEY:" in line), None)
        PUBLIC_KEY_SECURELOCK_RES = (
            publicKeyLine.replace(".*PUBLIC_KEY:\s*", "").strip()
            if publicKeyLine
            else ""
        )
    except subprocess.CalledProcessError as e:
        print(f"Could not fetch PUBLIC_KEY_SECURELOCK {e}")
        PUBLIC_KEY_SECURELOCK_RES = ""

    if not PUBLIC_KEY_SECURELOCK_RES:
        print("\n\nIt seems that your machine is not SGX compatible.\n")
        should_generate_certificates = prompt(
            "Do you want to continue by generating the necessary certificates using the Ethernity Cloud public certificate extraction services? (yes/no) (default: no):",
            default_value="no",
        ).lower()
        print()
        if should_generate_certificates != "yes":
            print("Exiting.")
            sys.exit(1)
        print()
        print(
            "\nGenerating certificates using the Ethernity Cloud signing service...\n"
        )
        print("**** Started ipfs initial pining ****")
        if os.path.exists("IPFS_HASH.ipfs"):
            os.remove("IPFS_HASH.ipfs")
        if os.path.exists("IPFS_DOCKER_COMPOSE_HASH.ipfs"):
            os.remove("IPFS_DOCKER_COMPOSE_HASH.ipfs")
        print("Upload docker-compose-final.yml to IPFS")
        import ethernity_cloud_sdk_py.commands.pynithy.ipfs_client as ipfs_client

        ipfs_client.main(
            host=os.getenv("IPFS_ENDPOINT", "localhost"),
            action="upload",
            filePath="docker-compose-final.yml",
        )
        if not os.path.exists("IPFS_DOCKER_COMPOSE_HASH.ipfs"):
            print("Error: Could not upload docker-compose-final.yml to IPFS")
            sys.exit(1)
        with open("IPFS_DOCKER_COMPOSE_HASH.ipfs", "r") as f:
            IPFS_DOCKER_COMPOSE_HASH = f.read().strip()
        write_env("IPFS_DOCKER_COMPOSE_HASH", IPFS_DOCKER_COMPOSE_HASH)

        print("Upload docker registry to IPFS")
        ipfs_client.main(
            host=os.getenv("IPFS_ENDPOINT", "localhost"),
            action="upload",
            folderPath=registry_path,
        )
        if not os.path.exists("IPFS_HASH.ipfs"):
            print("Error: Could not upload docker registry to IPFS")
            sys.exit(1)
        with open("IPFS_HASH.ipfs", "r") as f:
            IPFS_HASH = f.read().strip()
        write_env("IPFS_HASH", IPFS_HASH)
        print("**** Finished ipfs initial pining ****")

        print("**** Started certificate generation ****")
        print("Requesting certificates from Ethernity Cloud...")
        print()

        import ethernity_cloud_sdk_py.commands.pynithy.run.public_key_service as public_key_service

        public_key_service.main(
            enclave_name=os.getenv("PROJECT_NAME", ""),
            protocol_version="v3",
            network=os.getenv("BLOCKCHAIN_NETWORK", ""),
            template_version=os.getenv("VERSION", ""),
        )

        if os.path.exists("PUBLIC_KEY.txt"):
            with open("PUBLIC_KEY.txt", "r") as f:
                PUBLIC_KEY_SECURELOCK_RES = f.read().strip()

        if (
            not PUBLIC_KEY_SECURELOCK_RES
            or "-----BEGIN CERTIFICATE-----" not in PUBLIC_KEY_SECURELOCK_RES
        ):
            print("Error: Could not fetch PUBLIC_KEY_SECURELOCK")
            sys.exit(1)

        # const CERTIFICATE_CONTENT_SECURELOCK = PUBLIC_KEY_SECURELOCK_RES.match(/-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----/s)[1].trim();
        # if (!CERTIFICATE_CONTENT_SECURELOCK) {
        #     console.error("ERROR! PUBLIC_KEY_SECURELOCK not found");
        #     process.exit(1);
        # } else {
        #     console.log("FOUND PUBLIC_KEY_SECURELOCK");
        # }
        # fs.writeFileSync('certificate.securelock.crt', PUBLIC_KEY_SECURELOCK_RES);
        # console.log("Listing certificate PUBLIC_KEY_SECURELOCK:");
        # console.log(fs.readFileSync('certificate.securelock.crt', 'utf8'))
        CERTIFICATE_CONTENT_SECURELOCK = (
            re.search(
                r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----",
                PUBLIC_KEY_SECURELOCK_RES,
                re.DOTALL,
            )
            .group(1)  # type: ignore
            .strip()
        )
        if not CERTIFICATE_CONTENT_SECURELOCK:
            print("Error: PUBLIC_KEY_SECURELOCK not found")
            sys.exit(1)
        CERTIFICATE_CONTENT_SECURELOCK = (
        "-----BEGIN CERTIFICATE-----\n"+CERTIFICATE_CONTENT_SECURELOCK+"\n-----END CERTIFICATE-----"
    )
        with open("certificate.securelock.crt", "w") as f:
            f.write(CERTIFICATE_CONTENT_SECURELOCK)
        print("Listing certificate PUBLIC_KEY_SECURELOCK:")
        print(CERTIFICATE_CONTENT_SECURELOCK)

        print("**** Finished certificate generation ****")

    print()
    print("getting PUBLIC_KEY_TRUSTEDZONE")
    if os.path.exists("certificate.trustedzone.crt"):
        os.remove("certificate.trustedzone.crt")

    import ethernity_cloud_sdk_py.commands.pynithy.run.image_registry as image_registry

    try:
        result = image_registry.main(
            os.getenv("BLOCKCHAIN_NETWORK", ""),
            templateName,
            "v3",
            "",
            "getTrustedZoneCert",
        )
    except Exception as e:
        print(e)
        result = ""

    trustedZoneCert = (
        re.search(
            r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----",
            result,  # type: ignore
            re.DOTALL,
        )
        .group(1)  # type: ignore
        .strip()
    )

    print("Listing certificate PUBLIC_KEY_TRUSTEDZONE:")
    print(trustedZoneCert)
    PUBLIC_KEY_TRUSTEDZONE = (
        "-----BEGIN CERTIFICATE-----\n"+trustedZoneCert+"\n-----END CERTIFICATE-----"
    )
    with open("certificate.trustedzone.crt", "w") as f:
        f.write(PUBLIC_KEY_TRUSTEDZONE)

    # copy both certificates to the registry folder
    shutil.copy("certificate.securelock.crt", registry_path)
    shutil.copy("certificate.trustedzone.crt", registry_path)

    # remove IPFS_HASH.ipfs files to upload them again with the certificates included
    if os.path.exists("IPFS_HASH.ipfs"):
        os.remove("IPFS_HASH.ipfs")

    print()
    print("**** Started ipfs pining ****")
    ipfs_client.main(
        host=os.getenv("IPFS_ENDPOINT", "localhost"),
        action="upload",
        folderPath=registry_path,
    )
    if not os.path.exists("IPFS_HASH.ipfs"):
        print("Error: Could not upload docker registry to IPFS")
        sys.exit(1)
    with open("IPFS_HASH.ipfs", "r") as f:
        IPFS_HASH = f.read().strip()
    write_env("IPFS_HASH", IPFS_HASH)
    print("**** Finished ipfs pining ****")
    print()
    os.chdir(current_dir)
    print("Adding certificates for SECURELOCK into IMAGE REGISTRY smart contract...")
    print()
    try:
        image_registry.main(
            os.getenv("BLOCKCHAIN_NETWORK", ""),
            os.getenv("PROJECT_NAME", ""),
            os.getenv("VERSION", ""),
            os.getenv("PRIVATE_KEY", ""),
            "registerSecureLockImage",
        )
    except Exception as e:
        print(e)
        exit()

    print()
    print(
        (
            "Script completed successfully. You can start testing the application now. (eg. python src/ethernity_task.py)"
        )
    )


if __name__ == "__main__":
    main()
