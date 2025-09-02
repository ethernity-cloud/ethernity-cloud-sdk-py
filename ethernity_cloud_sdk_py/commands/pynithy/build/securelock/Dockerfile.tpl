FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers AS build-sgx-module

COPY src/get_sgx_report.c /etny-securelock/

RUN cd /etny-securelock/ && scone-gcc -shared -fPIC -O3 -o get_sgx_report.so get_sgx_report.c

FROM etny-securelock-serverless AS release

COPY ./src/serverless/requirements.txt /requirements.txt
RUN pip3 install -r /requirements.txt
RUN rm -rf /requirements.txt

ENV SECURELOCK_SESSION=__SECURELOCK_SESSION__
ENV BUCKET_NAME=__BUCKET_NAME__
ENV SMART_CONTRACT_ADDRESS=__SMART_CONTRACT_ADDRESS__
ENV IMAGE_REGISTRY_ADDRESS=__IMAGE_REGISTRY_ADDRESS__
ENV RPC_URL=__RPC_URL__
ENV CHAIN_ID=__CHAIN_ID__
ENV TRUSTED_ZONE_IMAGE=__TRUSTED_ZONE_IMAGE__
ENV NETWORK_TYPE=__NETWORK_TYPE__

RUN mkdir binary-fs-dir

COPY ./src /etny-securelock/
COPY ./scripts/* /etny-securelock/

COPY --from=build-sgx-module  /etny-securelock/get_sgx_report.so /etny-securelock/get_sgx_report.so

RUN /etny-securelock/binary-fs-build.sh

FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers AS build

COPY --from=release /binary-fs-dir /.

RUN scone gcc ./binary_fs_blob.s ./libbinary_fs_template.a -shared -o /libbinary-fs.so

FROM etny-securelock-serverless

COPY --from=build /usr/local/bin/scone /usr/local/bin/scone

#RUN scone cas attest scone-cas.cf 3061b9feb7fa67f3815336a085f629a13f04b0a1667c93b14ff35581dc8271e4 -GCS --only_for_testing-debug --only_for_testing-ignore-signer

COPY --from=build /libbinary-fs.so /lib/libbinary-fs.so

RUN openssl genrsa -3 -out /enclave-key.pem 3072


ENV SCONE_HEAP=__MEMORY_TO_ALLOCATE__
ENV SCONE_LOG=FATAL
ENV SCONE_DEBUG=0
ENV SCONE_STACK=4M
ENV SCONE_ALLOW_DLOPEN=2
ENV SCONE_EXTENSIONS_PATH=/lib/libbinary-fs.so

# Disabled production mode for testnet
__SCONE_SIGN__

RUN rm -rf /enclave-key.pem


ENTRYPOINT ["/usr/local/bin/python", "/etny-securelock/securelock.py"]
