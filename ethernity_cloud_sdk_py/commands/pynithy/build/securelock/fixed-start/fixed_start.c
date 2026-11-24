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
 * fixed_start_main checks the file tree, changes to the working directory
 * FIXED_START_PWD, drops those arguments and that environment, and calls the
 * program's main with the argument vector and the environment of this object.
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
 * The file tree check. fixed_start_main reads the file system the enclave
 * sees, from / down, through the C library's lstat, opendir, readdir,
 * readlink, open and read, before it calls the program's main, and ends the
 * process with status 78 unless every entry is the image's or one
 * FIXED_START_EXCEPT names.
 *
 * FIXED_START_EXCEPT names the entries that are not the image's, a record
 * each:
 *   { 'S', path, NULL }    the entry at path is not read, nor anything below
 *                          it;
 *   { 'N', path, names }   the entry at path, when there is one, is a
 *                          directory, and each of its entries bears one of
 *                          names. Such an entry with a record of its own
 *                          follows that record; any other is not a directory
 *                          and is not read.
 * Neither enters the digest.
 *
 * Every other entry enters the digest of the root, the SHA-256 of these
 * records, written depth first from /, the entries of each directory in
 * increasing bytewise order of their names, without following symbolic
 * links:
 *       a directory:          'D' path NUL, then the records of its entries
 *       a regular file:       'F' path NUL size NUL sha256 NUL
 *       a symbolic link:      'L' path NUL target NUL
 *       any other entry:      'O' path NUL
 * path is the entry's absolute path, size the number of bytes the file holds
 * in decimal, sha256 the SHA-256 of those bytes in lowercase hexadecimal,
 * target the link's content; / itself has no record. The start is refused
 * unless the digest is FIXED_START_ROOT_DIGEST, which tree_digest.sh writes
 * from the root the binary-fs is made of.
 *
 * An entry that cannot be read, an entry more than FIXED_START_DEPTH_MAX
 * names below /, a path of FIXED_START_PATH_MAX bytes or more and a directory
 * the launcher cannot hold in memory are refused with status 78.
 *
 * The enclave's header, given to the compiler with -include, defines:
 *   FIXED_START_ARGS     the argument vector: string literals, one per
 *                        argument, with a "\0" literal between two arguments;
 *   FIXED_START_ENV      the fixed variables: "KEY=VALUE" string literals,
 *                        each followed by a comma, or nothing;
 *   FIXED_START_ALLOWED  the keys passed through: string literals, each
 *                        followed by a comma;
 *   FIXED_START_PWD      the working directory, a string literal;
 *   FIXED_START_EXCEPT   the records of the entries that are not the image's,
 *                        each followed by a comma; the names of an 'N' record
 *                        are string literals back to back, each ended by
 *                        "\0".
 * The generated header tree_digest.sh writes, given with a second -include,
 * defines:
 *   FIXED_START_ROOT_DIGEST  the digest of the root, a string literal.
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

#if !defined(FIXED_START_ARGS) || !defined(FIXED_START_ENV) || !defined(FIXED_START_ALLOWED) \
    || !defined(FIXED_START_PWD) || !defined(FIXED_START_EXCEPT)
#error "the enclave's header defines FIXED_START_ARGS, FIXED_START_ENV, FIXED_START_ALLOWED, FIXED_START_PWD and FIXED_START_EXCEPT"
#endif
#if !defined(FIXED_START_ROOT_DIGEST)
#error "the header tree_digest.sh writes defines FIXED_START_ROOT_DIGEST"
#endif

#define FIXED_START_EXIT_REFUSED 78
#define FIXED_START_SPACE 32768
#define FIXED_START_MAX_ARGS 8
#define FIXED_START_PATH_MAX 4096
#define FIXED_START_DEPTH_MAX 64
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
static const char fixed_start_pwd[] = FIXED_START_PWD;
static const char fixed_start_root_digest[] = FIXED_START_ROOT_DIGEST;

struct fixed_start_exception {
    char kind;
    const char *path;
    const char *names;
};
static const struct fixed_start_exception fixed_start_except[] = { FIXED_START_EXCEPT { 0, NULL, NULL } };

#define FIXED_START_ENV_SLOTS \
    (sizeof fixed_start_env / sizeof fixed_start_env[0] \
     + sizeof fixed_start_allowed / sizeof fixed_start_allowed[0])

static char *fixed_start_argv[FIXED_START_MAX_ARGS + 1];
static int fixed_start_argc;
static char *fixed_start_environ[FIXED_START_ENV_SLOTS];
static char fixed_start_space[FIXED_START_SPACE];
static fixed_start_main_fn fixed_start_program_main;
static unsigned char fixed_start_read_buf[FIXED_START_READ_SIZE];
/* The path of the entry the check is at, and the content of a link. */
static char fixed_start_path[FIXED_START_PATH_MAX];
static char fixed_start_link[FIXED_START_PATH_MAX];

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

static void fixed_start_not_the_images(const char *path)
{
    fixed_start_refuse2("the file tree is not the image's at ", path);
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

/* The names of the entries of the directory at path, . and .. aside, in
   increasing bytewise order; *count is their number. */
static char **fixed_start_names(const char *path, size_t *count)
{
    DIR *dir = opendir(path);
    struct dirent *e;
    char **names = NULL;
    size_t n = 0;
    size_t cap = 0;

    if (dir == NULL)
        fixed_start_refuse2("a directory of the tree cannot be read: ", path);
    for (;;) {
        errno = 0;
        e = readdir(dir);
        if (e == NULL)
            break;
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
            continue;
        if (n == cap) {
            char **grown;

            cap = cap == 0 ? 64 : cap * 2;
            grown = realloc(names, cap * sizeof *names);
            if (grown == NULL)
                fixed_start_refuse2("a directory of the tree does not fit the launcher's memory: ", path);
            names = grown;
        }
        names[n] = strdup(e->d_name);
        if (names[n] == NULL)
            fixed_start_refuse2("a directory of the tree does not fit the launcher's memory: ", path);
        n++;
    }
    if (errno != 0)
        fixed_start_refuse2("a directory of the tree cannot be read: ", path);
    closedir(dir);
    if (n > 1)
        qsort(names, n, sizeof *names, fixed_start_name_order);
    *count = n;
    return names;
}

/* Appends /name to fixed_start_path, which holds len bytes (0 for /), and
   returns the new length. depth is the number of names the new path holds. */
static size_t fixed_start_descend(size_t len, const char *name, unsigned depth)
{
    size_t n = strlen(name);

    if (len + 1 + n >= FIXED_START_PATH_MAX)
        fixed_start_refuse2("a path of the tree is longer than the launcher takes: ", len == 0 ? "/" : fixed_start_path);
    fixed_start_path[len] = '/';
    memcpy(fixed_start_path + len + 1, name, n + 1);
    if (depth > FIXED_START_DEPTH_MAX)
        fixed_start_refuse2("a path of the tree is deeper than the launcher takes: ", fixed_start_path);
    return len + 1 + n;
}

/* The record of FIXED_START_EXCEPT for path, or NULL. */
static const struct fixed_start_exception *fixed_start_excepted(const char *path)
{
    size_t k;

    for (k = 0; fixed_start_except[k].path != NULL; k++) {
        if (strcmp(path, fixed_start_except[k].path) == 0)
            return &fixed_start_except[k];
    }
    return NULL;
}

/* Whether name is one of names: strings back to back, an empty one last. */
static int fixed_start_named(const char *names, const char *name)
{
    const char *p;

    for (p = names; *p != '\0'; p += strlen(p) + 1) {
        if (strcmp(p, name) == 0)
            return 1;
    }
    return 0;
}

/* Checks the entry at fixed_start_path (len bytes, depth names) against its
   record x of FIXED_START_EXCEPT. */
static void fixed_start_check_excepted(const struct fixed_start_exception *x, size_t len, unsigned depth)
{
    struct stat st;
    char **names;
    size_t count;
    size_t i;

    if (x->kind != 'N')
        return;
    if (lstat(fixed_start_path, &st) != 0)
        fixed_start_refuse2("an entry of the tree cannot be read: ", fixed_start_path);
    if (!S_ISDIR(st.st_mode))
        fixed_start_not_the_images(fixed_start_path);
    names = fixed_start_names(fixed_start_path, &count);
    for (i = 0; i < count; i++) {
        size_t child = fixed_start_descend(len, names[i], depth + 1);
        const struct fixed_start_exception *y;

        if (!fixed_start_named(x->names, names[i]))
            fixed_start_not_the_images(fixed_start_path);
        y = fixed_start_excepted(fixed_start_path);
        if (y != NULL) {
            fixed_start_check_excepted(y, child, depth + 1);
        } else {
            if (lstat(fixed_start_path, &st) != 0)
                fixed_start_refuse2("an entry of the tree cannot be read: ", fixed_start_path);
            if (S_ISDIR(st.st_mode))
                fixed_start_not_the_images(fixed_start_path);
        }
        fixed_start_path[len] = '\0';
        free(names[i]);
    }
    free(names);
}

/* Records the entries of the image's directory at fixed_start_path (len
   bytes, 0 for /; depth names) and everything below them, and checks the
   entries FIXED_START_EXCEPT names. */
static void fixed_start_record_directory(struct fixed_start_sha256 *s, size_t len, unsigned depth)
{
    size_t count;
    char **names = fixed_start_names(len == 0 ? "/" : fixed_start_path, &count);
    size_t i;

    for (i = 0; i < count; i++) {
        size_t child = fixed_start_descend(len, names[i], depth + 1);
        const struct fixed_start_exception *x = fixed_start_excepted(fixed_start_path);
        struct stat st;

        if (x != NULL) {
            fixed_start_check_excepted(x, child, depth + 1);
        } else if (lstat(fixed_start_path, &st) != 0) {
            fixed_start_refuse2("an entry of the tree cannot be read: ", fixed_start_path);
        } else if (S_ISDIR(st.st_mode)) {
            fixed_start_record(s, 'D', fixed_start_path, NULL, NULL);
            fixed_start_record_directory(s, child, depth + 1);
        } else if (S_ISREG(st.st_mode)) {
            fixed_start_record_file(s, fixed_start_path);
        } else if (S_ISLNK(st.st_mode)) {
            ssize_t n = readlink(fixed_start_path, fixed_start_link, sizeof fixed_start_link);

            if (n < 0 || (size_t)n >= sizeof fixed_start_link)
                fixed_start_refuse2("a link of the tree cannot be read: ", fixed_start_path);
            fixed_start_link[n] = '\0';
            fixed_start_record(s, 'L', fixed_start_path, fixed_start_link, NULL);
        } else {
            fixed_start_record(s, 'O', fixed_start_path, NULL, NULL);
        }
        fixed_start_path[len] = '\0';
        free(names[i]);
    }
    free(names);
}

/* Ends the process with status 78 unless the file system from / down holds
   the entries FIXED_START_ROOT_DIGEST was written for and, beside them, the
   entries FIXED_START_EXCEPT names alone. */
static void fixed_start_check_tree(void)
{
    struct fixed_start_sha256 s;
    char hex[65];

    fixed_start_sha256_init(&s);
    fixed_start_path[0] = '\0';
    fixed_start_record_directory(&s, 0, 0);
    fixed_start_sha256_hex(&s, hex);
    if (strcmp(hex, fixed_start_root_digest) != 0)
        fixed_start_not_the_images("/");
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
    if (chdir(fixed_start_pwd) != 0)
        fixed_start_refuse2("the working directory cannot be entered: ", fixed_start_pwd);
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
