FROM etny-securelock-serverless:latest AS release

ENV ENCLAVE_NAME_SECURELOCK=__ENCLAVE_NAME_SECURELOCK__
ENV BUCKET_NAME=__BUCKET_NAME__
ENV SMART_CONTRACT_ADDRESS=__SMART_CONTRACT_ADDRESS__
ENV IMAGE_REGISTRY_ADDRESS=__IMAGE_REGISTRY_ADDRESS__
ENV RPC_URL=__RPC_URL__
ENV CHAIN_ID=__CHAIN_ID__
ENV TRUSTED_ZONE_IMAGE=__TRUSTED_ZONE_IMAGE__


COPY ./src/serverless/requirements.txt /requirements.txt
RUN pip3 install -r /requirements.txt
RUN rm -rf /requirements.txt

RUN mkdir binary-fs-dir

COPY ./src /etny-securelock/
COPY ./scripts/* /etny-securelock/

RUN /etny-securelock/binary-fs-build.sh

FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers AS build

COPY --from=release /binary-fs-dir /.

RUN scone gcc ./binary_fs_blob.s ./libbinary_fs_template.a -shared -o /libbinary-fs.so

FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/__IMAGE_PATH__/python3.10.5-alpine3.15-scone5.8-pre-release

COPY --from=build /usr/local/bin/scone /usr/local/bin/scone

#RUN scone cas attest scone-cas.cf 3061b9feb7fa67f3815336a085f629a13f04b0a1667c93b14ff35581dc8271e4 -GCS --only_for_testing-debug --only_for_testing-ignore-signer

COPY --from=build /libbinary-fs.so /lib/libbinary-fs.so

RUN openssl genrsa -3 -out /enclave-key.pem 3072


ENV SCONE_HEAP=1024M
ENV SCONE_LOG=FATAL
ENV SCONE_STACK=4M
ENV SCONE_ALLOW_DLOPEN=1
ENV SCONE_EXTENSIONS_PATH=/lib/libbinary-fs.so

# Disabled production mode for testnet
# RUN scone-signer sign --key=/enclave-key.pem --env --production /usr/local/bin/python3

RUN rm -rf /enclave-key.pem


ENTRYPOINT ["/usr/local/bin/python", "/etny-securelock/securelock.py"]
