/*
 * The fixed start of an enclave.
 *
 * This object is linked into the enclave's signed extension,
 * /lib/libbinary-fs.so. SCONE's loader resolves the program's symbols in the
 * extension before the C library, so the program's start code calls the
 * __libc_start_main below, which hands the C library fixed_start_main in place
 * of the program's main. The C library calls fixed_start_main once the
 * constructors of every object have run, with the arguments and the
 * environment the runtime took from the host or from a CAS session.
 * fixed_start_main checks the file tree, drops those arguments and that
 * environment, and calls the program's main with the argument vector and the
 * environment of this object.
 *
 * The constructor runs before the program's own constructors. It ends the
 * process with status 78 when the start did not come through the
 * __libc_start_main below, and it installs the fixed environment.
 *
 * The fixed environment holds the variables FIXED_START_ENV names and, for
 * each key FIXED_START_ALLOWED names, the first entry of the environment the
 * runtime holds that sets that key, copied as it is. The copies take at most
 * FIXED_START_SPACE bytes; a start that needs more is refused with status 78.
 * Every other variable is dropped. The constructor copies the entries from the
 * environment it finds, fixed_start_main again from the environment the C
 * library hands it.
 *
 * The file tree check. FIXED_START_TREE names paths, each with the digest of
 * the tree at that path in the build stage that made the binary-fs, written
 * by tree_digest.sh. fixed_start_main computes the digest of each path as the
 * enclave sees it, through the C library's lstat, opendir, readdir, readlink,
 * open and read, before it calls the program's main, and ends the process with
 * status 78 when one differs. The digest of a path P is the SHA-256 of these
 * records, written in order:
 *   - P does not exist:      'A' P NUL
 *   - otherwise, for P and, when P is a directory, every entry below it at
 *     every depth, without following symbolic links, in depth-first order
 *     with the entries of each directory in increasing bytewise order of
 *     their names:
 *       a regular file:       'F' path NUL size NUL sha256 NUL
 *       a symbolic link:      'L' path NUL target NUL
 *       a directory:          no record
 *       any other entry:      'O' path NUL
 *     path is the entry's absolute path (P, '/', the names below P joined
 *     with '/'), size the number of bytes the file holds in decimal, sha256
 *     the SHA-256 of those bytes in lowercase hexadecimal, target the link's
 *     content.
 * An entry that cannot be read, a path longer than FIXED_START_PATH_MAX and
 * a directory the launcher cannot hold in memory are refused with status 78.
 *
 * The enclave's header, given to the compiler with -include, defines:
 *   FIXED_START_ARGS     the argument vector: string literals, one per
 *                        argument, with a "\0" literal between two arguments;
 *   FIXED_START_ENV      the fixed variables: "KEY=VALUE" string literals,
 *                        each followed by a comma, or nothing;
 *   FIXED_START_ALLOWED  the keys passed through: string literals, each
 *                        followed by a comma.
 * The generated header tree_digest.sh writes, given with a second -include,
 * defines:
 *   FIXED_START_TREE     the paths checked: { "path", "digest" } pairs, each
 *                        followed by a comma.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#if !defined(FIXED_START_ARGS) || !defined(FIXED_START_ENV) || !defined(FIXED_START_ALLOWED)
#error "the enclave's header defines FIXED_START_ARGS, FIXED_START_ENV and FIXED_START_ALLOWED"
#endif
#if !defined(FIXED_START_TREE)
#error "the header tree_digest.sh writes defines FIXED_START_TREE"
#endif

#define FIXED_START_EXIT_REFUSED 78
#define FIXED_START_SPACE 32768
#define FIXED_START_MAX_ARGS 8
#define FIXED_START_PATH_MAX 4096
#define FIXED_START_READ_SIZE 65536

extern char **environ;

typedef int (*fixed_start_main_fn)(int, char **, char **);
typedef int (*fixed_start_libc_fn)(fixed_start_main_fn, int, char **,
                                   void (*)(void), void (*)(void), void (*)(void));

/* The fixed arguments, back to back in one writable block: libuv's
   uv_setup_args() takes argv[0]..argv[argc-1] to be adjacent in memory. */
static char fixed_start_args[] = FIXED_START_ARGS;
static const char *const fixed_start_env[] = { FIXED_START_ENV NULL };
static const char *const fixed_start_allowed[] = { FIXED_START_ALLOWED NULL };

struct fixed_start_tree_path {
    const char *path;
    const char *digest;
};
static const struct fixed_start_tree_path fixed_start_tree[] = { FIXED_START_TREE { NULL, NULL } };

#define FIXED_START_ENV_SLOTS \
    (sizeof fixed_start_env / sizeof fixed_start_env[0] \
     + sizeof fixed_start_allowed / sizeof fixed_start_allowed[0])

static char *fixed_start_argv[FIXED_START_MAX_ARGS + 1];
static int fixed_start_argc;
static char *fixed_start_environ[FIXED_START_ENV_SLOTS];
static char fixed_start_space[FIXED_START_SPACE];
static fixed_start_main_fn fixed_start_program_main;
static unsigned char fixed_start_read_buf[FIXED_START_READ_SIZE];

static void fixed_start_refuse2(const char *reason, const char *detail)
{
    static const char head[] = "fixed start: the start is refused: ";
    char line[256];
    size_t n = sizeof head - 1;
    size_t r = strlen(reason);
    size_t d = detail != NULL ? strlen(detail) : 0;
    ssize_t w;

    memcpy(line, head, n);
    if (r > sizeof line - n - 1)
        r = sizeof line - n - 1;
    memcpy(line + n, reason, r);
    n += r;
    if (d > sizeof line - n - 1)
        d = sizeof line - n - 1;
    if (d > 0)
        memcpy(line + n, detail, d);
    n += d;
    line[n++] = '\n';
    w = write(2, line, n);
    (void)w;
    _exit(FIXED_START_EXIT_REFUSED);
}

static void fixed_start_refuse(const char *reason)
{
    fixed_start_refuse2(reason, NULL);
}

/* SHA-256 (FIPS 180-4). */
struct fixed_start_sha256 {
    uint32_t state[8];
    uint64_t bytes;
    unsigned char block[64];
    size_t used;
};

static const uint32_t fixed_start_sha256_k[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

#define FIXED_START_ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void fixed_start_sha256_block(struct fixed_start_sha256 *s, const unsigned char *p)
{
    uint32_t w[64];
    uint32_t a, b, c, d, e, f, g, h;
    int i;

    for (i = 0; i < 16; i++)
        w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16
             | (uint32_t)p[4 * i + 2] << 8 | (uint32_t)p[4 * i + 3];
    for (i = 16; i < 64; i++) {
        uint32_t s0 = FIXED_START_ROR(w[i - 15], 7) ^ FIXED_START_ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = FIXED_START_ROR(w[i - 2], 17) ^ FIXED_START_ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    a = s->state[0];
    b = s->state[1];
    c = s->state[2];
    d = s->state[3];
    e = s->state[4];
    f = s->state[5];
    g = s->state[6];
    h = s->state[7];
    for (i = 0; i < 64; i++) {
        uint32_t t1 = h + (FIXED_START_ROR(e, 6) ^ FIXED_START_ROR(e, 11) ^ FIXED_START_ROR(e, 25))
                    + ((e & f) ^ (~e & g)) + fixed_start_sha256_k[i] + w[i];
        uint32_t t2 = (FIXED_START_ROR(a, 2) ^ FIXED_START_ROR(a, 13) ^ FIXED_START_ROR(a, 22))
                    + ((a & b) ^ (a & c) ^ (b & c));
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }
    s->state[0] += a;
    s->state[1] += b;
    s->state[2] += c;
    s->state[3] += d;
    s->state[4] += e;
    s->state[5] += f;
    s->state[6] += g;
    s->state[7] += h;
}

static void fixed_start_sha256_init(struct fixed_start_sha256 *s)
{
    static const uint32_t initial[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    };

    memcpy(s->state, initial, sizeof initial);
    s->bytes = 0;
    s->used = 0;
}

static void fixed_start_sha256_update(struct fixed_start_sha256 *s, const void *data, size_t len)
{
    const unsigned char *p = data;

    s->bytes += len;
    while (len > 0) {
        size_t take = sizeof s->block - s->used;

        if (take > len)
            take = len;
        memcpy(s->block + s->used, p, take);
        s->used += take;
        p += take;
        len -= take;
        if (s->used == sizeof s->block) {
            fixed_start_sha256_block(s, s->block);
            s->used = 0;
        }
    }
}

/* Writes the digest as 64 lowercase hexadecimal digits and a NUL into hex. */
static void fixed_start_sha256_hex(struct fixed_start_sha256 *s, char hex[65])
{
    static const char digits[] = "0123456789abcdef";
    uint64_t bits = s->bytes * 8;
    unsigned char tail[72];
    size_t pad = (s->used < 56 ? 56 : 120) - s->used;
    int i;

    memset(tail, 0, sizeof tail);
    tail[0] = 0x80;
    for (i = 0; i < 8; i++)
        tail[pad + (size_t)i] = (unsigned char)(bits >> (56 - 8 * i));
    fixed_start_sha256_update(s, tail, pad + 8);
    for (i = 0; i < 32; i++) {
        unsigned char byte = (unsigned char)(s->state[i / 4] >> (24 - 8 * (i % 4)));

        hex[2 * i] = digits[byte >> 4];
        hex[2 * i + 1] = digits[byte & 15];
    }
    hex[64] = '\0';
}

static void fixed_start_record(struct fixed_start_sha256 *s, char type, const char *path,
                               const char *a, const char *b)
{
    fixed_start_sha256_update(s, &type, 1);
    fixed_start_sha256_update(s, path, strlen(path) + 1);
    if (a != NULL)
        fixed_start_sha256_update(s, a, strlen(a) + 1);
    if (b != NULL)
        fixed_start_sha256_update(s, b, strlen(b) + 1);
}

static void fixed_start_record_file(struct fixed_start_sha256 *s, const char *path)
{
    struct fixed_start_sha256 content;
    char hex[65];
    char size[24];
    uint64_t total = 0;
    size_t n = sizeof size - 1;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);

    if (fd < 0)
        fixed_start_refuse2("a file of the tree cannot be opened: ", path);
    fixed_start_sha256_init(&content);
    for (;;) {
        ssize_t got = read(fd, fixed_start_read_buf, sizeof fixed_start_read_buf);

        if (got < 0 && errno == EINTR)
            continue;
        if (got < 0)
            fixed_start_refuse2("a file of the tree cannot be read: ", path);
        if (got == 0)
            break;
        fixed_start_sha256_update(&content, fixed_start_read_buf, (size_t)got);
        total += (uint64_t)got;
    }
    close(fd);
    fixed_start_sha256_hex(&content, hex);
    size[n] = '\0';
    do {
        size[--n] = (char)('0' + total % 10);
        total /= 10;
    } while (total > 0);
    fixed_start_record(s, 'F', path, size + n, hex);
}

static int fixed_start_name_order(const void *x, const void *y)
{
    return strcmp(*(char *const *)x, *(char *const *)y);
}

/* Records the entry at path (len bytes, in a buffer of FIXED_START_PATH_MAX
   bytes) and, for a directory, every entry below it. */
static void fixed_start_record_entry(struct fixed_start_sha256 *s, char *path, size_t len)
{
    struct stat st;

    if (lstat(path, &st) != 0)
        fixed_start_refuse2("an entry of the tree cannot be read: ", path);
    if (S_ISREG(st.st_mode)) {
        fixed_start_record_file(s, path);
    } else if (S_ISLNK(st.st_mode)) {
        char target[FIXED_START_PATH_MAX];
        ssize_t n = readlink(path, target, sizeof target);

        if (n < 0 || (size_t)n >= sizeof target)
            fixed_start_refuse2("a link of the tree cannot be read: ", path);
        target[n] = '\0';
        fixed_start_record(s, 'L', path, target, NULL);
    } else if (S_ISDIR(st.st_mode)) {
        DIR *dir = opendir(path);
        struct dirent *e;
        char **names = NULL;
        size_t count = 0;
        size_t cap = 0;
        size_t i;

        if (dir == NULL)
            fixed_start_refuse2("a directory of the tree cannot be read: ", path);
        for (;;) {
            errno = 0;
            e = readdir(dir);
            if (e == NULL)
                break;
            if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
                continue;
            if (count == cap) {
                char **grown;

                cap = cap == 0 ? 64 : cap * 2;
                grown = realloc(names, cap * sizeof *names);
                if (grown == NULL)
                    fixed_start_refuse2("a directory of the tree does not fit the launcher's memory: ", path);
                names = grown;
            }
            names[count] = strdup(e->d_name);
            if (names[count] == NULL)
                fixed_start_refuse2("a directory of the tree does not fit the launcher's memory: ", path);
            count++;
        }
        if (errno != 0)
            fixed_start_refuse2("a directory of the tree cannot be read: ", path);
        closedir(dir);
        if (count > 1)
            qsort(names, count, sizeof *names, fixed_start_name_order);
        for (i = 0; i < count; i++) {
            size_t name = strlen(names[i]);

            if (len + 1 + name >= FIXED_START_PATH_MAX)
                fixed_start_refuse2("a path of the tree is longer than the launcher takes: ", path);
            path[len] = '/';
            memcpy(path + len + 1, names[i], name + 1);
            fixed_start_record_entry(s, path, len + 1 + name);
            path[len] = '\0';
            free(names[i]);
        }
        free(names);
    } else {
        fixed_start_record(s, 'O', path, NULL, NULL);
    }
}

/* Ends the process with status 78 unless every path of FIXED_START_TREE has
   the digest the build wrote for it. */
static void fixed_start_check_tree(void)
{
    size_t k;

    for (k = 0; fixed_start_tree[k].path != NULL; k++) {
        static char path[FIXED_START_PATH_MAX];
        struct fixed_start_sha256 s;
        struct stat st;
        char hex[65];
        size_t len = strlen(fixed_start_tree[k].path);

        if (len >= sizeof path)
            fixed_start_refuse2("a path of the tree is longer than the launcher takes: ", fixed_start_tree[k].path);
        memcpy(path, fixed_start_tree[k].path, len + 1);
        fixed_start_sha256_init(&s);
        if (lstat(path, &st) != 0) {
            if (errno != ENOENT)
                fixed_start_refuse2("an entry of the tree cannot be read: ", path);
            fixed_start_record(&s, 'A', path, NULL, NULL);
        } else {
            fixed_start_record_entry(&s, path, len);
        }
        fixed_start_sha256_hex(&s, hex);
        if (strcmp(hex, fixed_start_tree[k].digest) != 0)
            fixed_start_refuse2("the file tree is not the image's at ", fixed_start_tree[k].path);
    }
}

static void fixed_start_build_args(void)
{
    char *p = fixed_start_args;
    char *end = fixed_start_args + sizeof fixed_start_args;

    fixed_start_argc = 0;
    while (p < end && fixed_start_argc < FIXED_START_MAX_ARGS) {
        fixed_start_argv[fixed_start_argc++] = p;
        p += strlen(p) + 1;
    }
    if (p < end)
        fixed_start_refuse("the fixed vector holds more arguments than the launcher takes");
    fixed_start_argv[fixed_start_argc] = NULL;
}

/* Copies entry into fixed_start_space at *used and returns the copy. */
static char *fixed_start_copy(const char *entry, size_t *used)
{
    size_t len = strlen(entry) + 1;
    char *copy;

    if (len > FIXED_START_SPACE - *used)
        fixed_start_refuse("the environment exceeds the launcher's space");
    copy = fixed_start_space + *used;
    memcpy(copy, entry, len);
    *used += len;
    return copy;
}

/* Fills fixed_start_environ: the fixed variables, then, for each allowed
   key, the first entry of from that sets it. */
static void fixed_start_install(char **from)
{
    size_t used = 0;
    size_t n = 0;
    size_t k;

    for (k = 0; fixed_start_env[k] != NULL; k++)
        fixed_start_environ[n++] = fixed_start_copy(fixed_start_env[k], &used);
    for (k = 0; fixed_start_allowed[k] != NULL; k++) {
        size_t key = strlen(fixed_start_allowed[k]);
        char **e;

        for (e = from; e != NULL && *e != NULL; e++) {
            if (strncmp(*e, fixed_start_allowed[k], key) == 0 && (*e)[key] == '=') {
                fixed_start_environ[n++] = fixed_start_copy(*e, &used);
                break;
            }
        }
    }
    fixed_start_environ[n] = NULL;
}

static int fixed_start_main(int argc, char **argv, char **envp)
{
    (void)argc;
    (void)argv;
    fixed_start_check_tree();
    fixed_start_build_args();
    if (envp != fixed_start_environ)
        fixed_start_install(envp);
    environ = fixed_start_environ;
    return fixed_start_program_main(fixed_start_argc, fixed_start_argv, fixed_start_environ);
}

int __libc_start_main(fixed_start_main_fn program_main, int argc, char **argv,
                      void (*init_fn)(void), void (*fini_fn)(void), void (*ldso_fn)(void))
{
    fixed_start_libc_fn next = (fixed_start_libc_fn)dlsym(RTLD_NEXT, "__libc_start_main");

    if (next == NULL)
        fixed_start_refuse("the C library's __libc_start_main was not found");
    fixed_start_program_main = program_main;
    return next(fixed_start_main, argc, argv, init_fn, fini_fn, ldso_fn);
}

__attribute__((constructor))
static void fixed_start_before_constructors(void)
{
    if (fixed_start_program_main == NULL)
        fixed_start_refuse("the start did not come through the launcher");
    fixed_start_install(environ);
    environ = fixed_start_environ;
}
