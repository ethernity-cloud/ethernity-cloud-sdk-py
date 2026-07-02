FROM __DOCKER_REPO_URL__:__BASE_IMAGE_TAG__ AS release

# The python-3.14.6-alpine3.24-scone6.0.7 base ships a static CPython 3.14 with
# pip and the complete securelock dependency set already installed (built
# off-SCONE and baked in): pyinstaller, web3, cryptography, ecdsa, pyasn1,
# tinyec, minio, pynacl, flask, flask_limiter, python-dotenv, eth_account,
# pycryptodome and their transitive deps. Nothing is pip-installed here because
# building/compiling under the SCONE-shielded interpreter does not work; the
# deps are already present.
#
# Only the small set of system tools the build/runtime scripts call is added.
RUN apk update
RUN apk add bash openrc bind-tools sudo binutils curl
