/*
 * The securelock's fixed start (fixed_start.c, included with -include into
 * the extension Dockerfile.tpl links).
 *
 * The link line defines FIXED_START_SESSION, a string literal: the name of
 * the securelock's CAS session, the one securelock.py reads its key and
 * certificate under.
 *
 * The argument vector is the image's ENTRYPOINT: the interpreter isolated
 * (-I: no PYTHON* variable is read, no user site directory and no script
 * directory is put on sys.path; securelock.py puts its own directory there),
 * and securelock.py.
 *
 * The variables set whatever the start holds:
 *   HOME=/tmp             a library that expands "~" ends the enclave without
 *                         it;
 *   OPENSSL_CONF          the OpenSSL configuration is the empty file the
 *                         image holds at that path (scripts/enclave-root.sh),
 *                         for every OpenSSL the image carries, not the one of
 *                         a library's compiled directory, which the image
 *                         does not hold;
 *   OPENSSL_MODULES, OPENSSL_ENGINES
 *                         the directory of that file, which holds nothing
 *                         else: OpenSSL finds no provider module and no
 *                         engine to load. The binary-fs holds no empty
 *                         directory, so the directory holds that one file.
 *
 * The keys passed through are the ones securelock.py and its modules read
 * from their environment:
 *   ETNY_ORDER_ID         the order; read_env_str() takes it from the bucket
 *                         .env when the environment does not set it.
 *   ECAS_ATTEST_SESSION, ECAS_ENCLAVE_NONCE
 *                         the session and the nonce the CAS injects and
 *                         re-attests.
 *   ECAS_CAS_QUOTE        the CAS's quote, checked against the baked
 *                         ValidatorRegistry.
 *   ESR_CONTRACT_ADDRESS  the ESR registry the image's environment and the CAS
 *                         session's name; the securelock runs no ESR without
 *                         it.
 * esr_wallet.py also reads ESR_IDENTITY_SECRET, which is not passed: no build
 * or session sets it, and it decides only whether the warning that the ESR
 * identity is reproducible is logged.
 *
 * The working directory is the entry's, the image's SCONE_PWD and WORKDIR
 * (Dockerfile.tpl).
 *
 * The entries of the enclave's file system that are not the image's:
 *   /dev, /proc           the runtime's device and process directories, not
 *                         read;
 *   /etc                  holds hosts and resolv.conf, the host's
 *                         name-resolution files the binary-fs passes through
 *                         (--host-path), and nothing else;
 *   /app, /private        hold the session's directory and nothing else,
 *                         which holds the files the CAS session places: the
 *                         certificate authority's certificate, the
 *                         certificate and the key.
 */
#if !defined(FIXED_START_SESSION)
#error "the build defines FIXED_START_SESSION: the name of the securelock's CAS session, a string literal"
#endif
_Static_assert(sizeof FIXED_START_SESSION > 1, "FIXED_START_SESSION is not empty");

#define FIXED_START_ARGS "/usr/local/bin/python" "\0" "-I" "\0" "/etny-securelock/securelock.py"
#define FIXED_START_ENV \
    "HOME=/tmp", \
    "OPENSSL_CONF=/etny-securelock/fixed-start/openssl.cnf", \
    "OPENSSL_MODULES=/etny-securelock/fixed-start", \
    "OPENSSL_ENGINES=/etny-securelock/fixed-start",
#define FIXED_START_ALLOWED \
    "ETNY_ORDER_ID", \
    "ECAS_ATTEST_SESSION", \
    "ECAS_ENCLAVE_NONCE", \
    "ECAS_CAS_QUOTE", \
    "ESR_CONTRACT_ADDRESS",
#define FIXED_START_PWD "/etny-securelock"
#define FIXED_START_EXCEPT \
    { 'S', "/dev", NULL }, \
    { 'S', "/proc", NULL }, \
    { 'N', "/etc", "hosts\0" "resolv.conf\0" }, \
    { 'N', "/app", FIXED_START_SESSION "\0" }, \
    { 'N', "/app/" FIXED_START_SESSION, "ca.pem\0" "cert.pem\0" }, \
    { 'N', "/private", FIXED_START_SESSION "\0" }, \
    { 'N', "/private/" FIXED_START_SESSION, "key.pem\0" },
