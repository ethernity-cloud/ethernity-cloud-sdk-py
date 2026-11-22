/*
 * The securelock's fixed start (fixed_start.c, included with -include into
 * the extension Dockerfile.tpl links).
 *
 * The argument vector is the image's ENTRYPOINT: the interpreter isolated
 * (-I: no PYTHON* variable is read, no user site directory and no script
 * directory is put on sys.path; securelock.py puts its own directory there),
 * and securelock.py.
 *
 * HOME=/tmp is set whatever the start holds: a library that expands "~" ends
 * the enclave without it.
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
 */
#define FIXED_START_ARGS "/usr/local/bin/python" "\0" "-I" "\0" "/etny-securelock/securelock.py"
#define FIXED_START_ENV "HOME=/tmp",
#define FIXED_START_ALLOWED \
    "ETNY_ORDER_ID", \
    "ECAS_ATTEST_SESSION", \
    "ECAS_ENCLAVE_NONCE", \
    "ECAS_CAS_QUOTE", \
    "ESR_CONTRACT_ADDRESS",
