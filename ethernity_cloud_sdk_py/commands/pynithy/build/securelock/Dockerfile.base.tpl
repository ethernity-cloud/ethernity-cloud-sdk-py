FROM __DOCKER_REPO_URL__:__BASE_IMAGE_TAG__ AS release

RUN apk update

RUN cd /
RUN apk add bash openrc bind-tools sudo binutils curl 
RUN pip3 install --upgrade pip
RUN pip3 install --upgrade setuptools
RUN pip3 install python-dotenv
RUN pip3 install web3==5.31.0
RUN pip3 install cryptography==42.0.7
RUN pip3 install ecdsa
RUN pip3 install pyasn1
RUN pip3 install tinyec
RUN pip3 install minio
RUN pip3 install pynacl
RUN pip3 install pyinstaller
