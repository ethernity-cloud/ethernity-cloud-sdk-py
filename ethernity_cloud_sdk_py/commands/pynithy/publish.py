import os
import sys
import time
import threading
import subprocess
import re
import json
import shutil
import requests
from os.path import join, dirname
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from requests.packages.urllib3 import disable_warnings

import ethernity_cloud_sdk_py.commands.pynithy.run.public_key_service as public_key_service
from ethernity_cloud_sdk_py.commands.pynithy.run.image_registry import ImageRegistry
from ethernity_cloud_sdk_py.commands.pynithy.ipfs_client import IPFSClient
from ethernity_cloud_sdk_py.commands.spinner import Spinner

import time


from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config

config = Config(Path(".config.json").resolve())
config.load()

image_registry = ImageRegistry()

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
        # Print the prompt and wait for user input
        user_input = input(f"{message} ").strip().lower()
        
        # If user presses Enter without input, display the default inline
        if not user_input:
            # Move the cursor up one line: \033[A
            # Then rewrite the line, this time showing the chosen default
            print(f"\033[A{message} {default_option}")
            return default_option
        elif user_input in options:
            return user_input
        else:
            print(
                f'\n\t\t\tInvalid option "{user_input}".\n\t\t\tPlease enter one of: {", ".join(options)}. Default value is: {default_option}\n'
            )

def extract_scone_hash(service):
    command = f"docker-compose run -e SCONE_LOG=INFO -e SCONE_HASH=1 {service}"
    try:
        output = (
            subprocess.check_output(
                command, shell=True, cwd=run_dir, stderr=subprocess.STDOUT
            )
            .decode()
            .strip()
        )
        # Extract SHA256 hash from the output
        sha256_pattern = r'\b[a-fA-F0-9]{64}\b'
        sha256_match = re.search(sha256_pattern, output)

        if sha256_match:
            sha256_hash = sha256_match.group(0)
            #print(f"Found SHA256 hash: {sha256_hash}")
            return sha256_hash
        else:
            raise Exception(f"No SHA256 hash found in the output.")

    except subprocess.CalledProcessError as e:
        raise Exception(f"Error while executing {command}: {e.output.decode().strip()}")


def process_yaml_template(template_file, output_file):
    
    config.write("IPFS_HASH", "")
    config.write("IPFS_DOCKER_COMPOSE_HASH","")
    config.write("IPFS_HASH_PUBLISH", "")

    MRENCLAVE_SECURELOCK = config.read("MRENCLAVE_SECURELOCK")
    SECURELOCK_SESSION = config.read("SECURELOCK_SESSION")
    
    PREDECESSOR_HASH_SECURELOCK = ""

    PREDECESSOR_HASH_SECURELOCK = config.read("PREDECESSOR_HASH_SECURELOCK")

    replacements = {
        "__PREDECESSOR__": (
            f""
            if PREDECESSOR_HASH_SECURELOCK == ""
            else f"predecessor: {PREDECESSOR_HASH_SECURELOCK}"
        ),
        "__MRENCLAVE__": MRENCLAVE_SECURELOCK,
        "__ENCLAVE_NAME__": SECURELOCK_SESSION,
    }

    if not os.path.exists(template_file):
        print(f"Error: Template file {template_file} not found!")
        exit(1)
    with open(template_file, "r") as f:
        content = f.read()
    for key, value in replacements.items():
        content = content.replace(f"{key}", value)
    with open(output_file, "w") as f:
        f.write(content)

    return True

def get_docker_server_info():
    try:
        # Run the 'docker info' command and capture the output
        result = subprocess.check_output("docker info", text=True, stderr=subprocess.DEVNULL)

        # Find the Server section in the output
        server_info_started = False
        server_info = []
        
        for line in result.splitlines():
            if server_info_started:
                if line.strip() == "":  # End of Server section
                    break
                server_info.append(line.strip())
            elif line.startswith("Server:"):
                server_info_started = True
                server_info.append(line.strip())
        if len(server_info) > 10:
            #subprocess.check_output("docker stop las", text=True, stderr=subprocess.DEVNULL)
            #subprocess.check_output("docker rm las", text=True, stderr=subprocess.DEVNULL)
            return True
        return False
    except subprocess.CalledProcessError as e:
        return False
    except FileNotFoundError:
        return False

def update_docker_compose_files():
    
    SECURELOCK_SESSION = config.read("SECURELOCK_SESSION")

    #if "testnet" in config.read("BLOCKCHAIN_NETWORK").lower():
    #    TRUSTEDZONE_SESSION = "etny-pynithy-trustedzone-v3-testnet-0.1.12"
    #else:
    #    TRUSTEDZONE_SESSION = "ecld-pynithy-trustedzone-v3-3.0.0"

    TRUSTEDZONE_IPFS_HASH = image_registry.get_trusted_zone_hash(config.read("TRUSTED_ZONE_IMAGE"), "v3")
    TRUSTEDZONE_SESSION = image_registry.get_trustezone_image_session(TRUSTEDZONE_IPFS_HASH)

    MEMORY_TO_ALLOCATE = config.read("MEMORY_TO_ALLOCATE")
    MEMORY_TO_ALLOCATE_FORMATED = f"{MEMORY_TO_ALLOCATE * 1024}M"
    try:
        # Backup and restore docker-compose templates
        backup_files = ["docker-compose.yml.tmpl", "docker-compose-final.yml.tmpl"]
        for file in backup_files:
            if not os.path.exists(file):
                print(f"Error: {file} not found!")
                continue
            shutil.copyfile(file, file.replace(".tmpl", ""))

        files = ["docker-compose.yml", "docker-compose-final.yml"]
        for file in files:
            if not os.path.exists(file):
                print(f"Error: {file} not found!")
                continue
            with open(file, "r") as f:
                content = f.read()

            content = (
                content
                .replace("__SECURELOCK_SESSION__", SECURELOCK_SESSION)
                .replace("__TRUSTEDZONE_SESSION__", TRUSTEDZONE_SESSION)
                .replace("__MEMORY_TO_ALLOCATE__", MEMORY_TO_ALLOCATE_FORMATED)
            )

            with open(file, "w") as f:
                f.write(content)
    except Exception as e:
        return False
    
    return True

def extract_public_key_local():
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
            lines = output.split("\n")
            publicKeyLine = next((line for line in lines if "PUBLIC_KEY:" in line), None)
            result = (
                publicKeyLine.replace(".*PUBLIC_KEY:\s*", "").strip()
                if publicKeyLine
                else ""
            )
        except subprocess.CalledProcessError as e:
            return False
        
        return result


def check_public_key_certificate():
        if os.path.exists("PUBLIC_KEY.txt"):
            with open("PUBLIC_KEY.txt", "r") as f:
                PUBLIC_KEY_SECURELOCK_RES = f.read().strip()

        if (
            not PUBLIC_KEY_SECURELOCK_RES
            or "-----BEGIN CERTIFICATE-----" not in PUBLIC_KEY_SECURELOCK_RES
        ):
            print("Error: Could not fetch PUBLIC_KEY_SECURELOCK")
            exit(1)

 
        #with open("certificate.securelock.crt", "w") as f:
        #    f.write(PUBLIC_KEY_SECURELOCK_RES)

        #print("# Finished certificate generation")

        #if os.path.exists("certificate.trustedzone.crt"):
        #    os.remove("certificate.trustedzone.crt")

        #try:
        #    PUBLIC_KEY_TRUSTEDZONE = image_registry.get_trusted_zone_public_key()
        #except Exception as e:
        #    print(e)

        #with open("certificate.trustedzone.crt", "w") as f:
        #    f.write(PUBLIC_KEY_TRUSTEDZONE)

        # copy both certificates to the registry folder
        # shutil.copy("certificate.securelock.crt", registry_path)
        # shutil.copy("certificate.trustedzone.crt", registry_path)

def generate_certificates():
    # Generate a key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )

    # Get the public key
    public_key = private_key.public_key()

    # Get SECURELOCK_SESSION from environment variable or default value
    organization_name = config.read(
        "SECURELOCK_SESSION"
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

def update_cas_session():
    # Read certificates and data
    with open("etny-securelock-test.yaml", "rb") as f:
        yaml_data = f.read()

    # Set up the request headers
    headers = {"Content-Type": "application/octet-stream"}

    # Perform the HTTPS POST request
    try:


        # Disable only the InsecureRequestWarning
        disable_warnings(InsecureRequestWarning)

        # Create a session to manage certificates and SSL settings
        session = requests.Session()
        session.verify = False  # Equivalent to rejectUnauthorized: false
        session.cert = ("cert.pem", "key.pem")  # Provide the client cert and key

        # Perform the POST request
        response = session.post(
            "https://scone-cas.cf:8081/session", data=yaml_data, headers=headers
        )

        with open("predecessor.json", "w", encoding="utf-8") as f:
            json.dump(response.json(), f, indent=2)

        response_data = response.json()
        predecessor_hash_securelock = response_data.get("hash", None)

        if predecessor_hash_securelock != None:
            config.write("PREDECESSOR_HASH_SECURELOCK", predecessor_hash_securelock)
        else:
            config.write("PREDECESSOR_HASH_SECURELOCK", "")

        if predecessor_hash_securelock == None:
            print("\t\u2718  Error: Could not update session file for securelock")
            print(
                "\n\tSession predecessor is lost and cannot be recovered. Please run ecld-build to increment the version number"
            )
            exit(1)

        return True

    except requests.RequestException as error:

        print("\t\u2714  Could not update session")
        print("\n\tError:", error)
        exit(1)
def main(private_key):
    spinner = Spinner()
    image_registry.set_private_key(private_key)
    ipfs_client = IPFSClient(config.read("IPFS_ENDPOINT"))
    BLOCKCHAIN_NETWORK = config.read("BLOCKCHAIN_NETWORK")

    IPFS_HASH = ""
    IPFS_DOCKER_COMPOSE_HASH = ""
    IPFS_HASH_PUBLISH = ""

    global current_dir, run_dir
    current_dir = os.getcwd()
    run_dir = Path(__file__).resolve().parent / "run"
    os.chdir(run_dir)
    registry_path = os.path.join(current_dir, "registry")
    config.write("REGISTRY_PATH", registry_path)

    templateName = config.read("TRUSTED_ZONE_IMAGE")
    isMainnet = False if "testnet" in templateName.lower() else True

    result = spinner.spin_till_done("Checking docker service", get_docker_server_info)

    if not result:
        print("Error: Docker version not found. Please install and run docker service.")
        exit(1)


    try:
        mrenclave_securelock = spinner.spin_till_done(
            "Calculating enclave hash",
            extract_scone_hash,
            "etny-securelock")
    except Exception as e:
        print(f"Error: {e}")
        exit(1)
        
    if mrenclave_securelock != config.read("MRENCLAVE_SECURELOCK"):

        config.write("MRENCLAVE_SECURELOCK", mrenclave_securelock)
        
        spinner.spin_till_done(
            "Deploying configuration template",
            process_yaml_template,
            "etny-securelock-test.yaml.tpl",
            "etny-securelock-test.yaml",
        )
        
        # Generate certificates if needed
        key_pem_path = "key.pem"
        cert_pem_path = "cert.pem"

        if (
            not os.path.exists(key_pem_path)
            and not os.path.exists(cert_pem_path)
        ):
            spinner.spin_till_done("Generating certificate for session registration", generate_certificates)

        spinner.spin_till_done("Registering session into CAS", update_cas_session)

        config.write("MRENCLAVE_SECURELOCK", mrenclave_securelock)

    else:
        IPFS_HASH = config.read("IPFS_HASH")
        IPFS_DOCKER_COMPOSE_HASH = config.read("IPFS_DOCKER_COMPOSE_HASH")

    spinner.spin_till_done("Updating docker composer files", update_docker_compose_files)

    print('\n\u276f\u276f Extracting public key from enclave')

    if os.path.exists("certificate.securelock.crt"):
        os.remove("certificate.securelock.crt")


    

    ENCLAVE_PUBLIC_KEY = spinner.spin_till_done("Extracing public key using local docker", extract_public_key_local)


    if not ENCLAVE_PUBLIC_KEY:
        print("\n\t\tTo publish the eclave, the public key needs to be extracted and for this SGX technology is required.\n\t\tIt seems that your machine is not configured to use SGX.\n")
        
        options = [ "y", "n", "yes", "no"]
        should_generate_certificates = prompt_options(
            "\t\tDo you want to use Ethernity Cloud public key extraction service? [Y/n]:",
            options,
            "y",
        ).lower()

        if should_generate_certificates != "y" and should_generate_certificates != "yes":
            print("\n\t\tPlease configure local SGX support and run the setup again")
            exit(1)

        print()
        if IPFS_HASH == "" or IPFS_DOCKER_COMPOSE_HASH == "":

            try:
                IPFS_DOCKER_COMPOSE_HASH = spinner.spin_till_done(
                    "Uploading and pinning docker compose file to IPFS",
                    ipfs_client.upload,
                    "docker-compose-final.yml"
                )
            except Exception as e:
                    print("\t\u2716  Could not upload docker-compose-final.yml to IPFS")
                    print(f"\t Error uploading: {e}")
                    exit(1)

            config.write("IPFS_DOCKER_COMPOSE_HASH", IPFS_DOCKER_COMPOSE_HASH)

            #IPFS_HASH = spinner.spin_till_done(
            #    "Uploading and pinning enclave to IPFS... ",
            #    ipfs_client.main,
            #    host=config.read("IPFS_ENDPOINT"),
            #    action="upload",
            #    folderPath=registry_path
            #)

            IPFS_HASH = ipfs_client.upload(registry_path)

            if not IPFS_HASH:
                print("\t\u2716  Error: Could not upload enclave to IPFS")
                exit(1)


            config.write("IPFS_HASH", IPFS_HASH)
        

        ENCLAVE_PUBLIC_KEY = public_key_service.main(
            enclave_name=config.read("PROJECT_NAME"),
            protocol_version="v3",
            network=config.read("BLOCKCHAIN_NETWORK"),
            template_version=config.read("VERSION"),
            ipfs_hash=IPFS_HASH,
            docker_composer_hash=IPFS_DOCKER_COMPOSE_HASH
        )


    os.chdir(current_dir)
    print(f'\n\u276f\u276f Registering enclave on {BLOCKCHAIN_NETWORK}')

    try:
        image_registry.register_securelock_image(ENCLAVE_PUBLIC_KEY)
    except Exception as e:
        print(e)
        exit()
