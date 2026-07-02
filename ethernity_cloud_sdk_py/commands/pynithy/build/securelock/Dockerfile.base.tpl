FROM __DOCKER_REPO_URL__:__BASE_IMAGE_TAG__ AS release

# The python-3.14.6-alpine3.24-scone6.0.7 base is a LEAN static CPython 3.14
# (built off-SCONE with scone-gcc) plus pip/setuptools/wheel only - no
# application dependencies are baked in. Users add their own Python modules via
# src/serverless/requirements.txt, which are pip-installed on a normal python
# during the PyInstaller freeze stage (see Dockerfile.tpl); nothing is compiled
# under the SCONE-shielded interpreter.
#
# Only the system tools the build/runtime scripts call are added here (openssl
# CLI is needed for the enclave key generation in the final securelock stage).
RUN apk update
RUN apk add bash openrc bind-tools sudo binutils curl openssl
