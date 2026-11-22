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
 * fixed_start_main drops them and calls the program's main with the argument
 * vector and the environment of this object.
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
 * The enclave's header, given to the compiler with -include, defines:
 *   FIXED_START_ARGS     the argument vector: string literals, one per
 *                        argument, with a "\0" literal between two arguments;
 *   FIXED_START_ENV      the fixed variables: "KEY=VALUE" string literals,
 *                        each followed by a comma, or nothing;
 *   FIXED_START_ALLOWED  the keys passed through: string literals, each
 *                        followed by a comma.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>

#if !defined(FIXED_START_ARGS) || !defined(FIXED_START_ENV) || !defined(FIXED_START_ALLOWED)
#error "the enclave's header defines FIXED_START_ARGS, FIXED_START_ENV and FIXED_START_ALLOWED"
#endif

#define FIXED_START_EXIT_REFUSED 78
#define FIXED_START_SPACE 32768
#define FIXED_START_MAX_ARGS 8

extern char **environ;

typedef int (*fixed_start_main_fn)(int, char **, char **);
typedef int (*fixed_start_libc_fn)(fixed_start_main_fn, int, char **,
                                   void (*)(void), void (*)(void), void (*)(void));

/* The fixed arguments, back to back in one writable block: libuv's
   uv_setup_args() takes argv[0]..argv[argc-1] to be adjacent in memory. */
static char fixed_start_args[] = FIXED_START_ARGS;
static const char *const fixed_start_env[] = { FIXED_START_ENV NULL };
static const char *const fixed_start_allowed[] = { FIXED_START_ALLOWED NULL };

#define FIXED_START_ENV_SLOTS \
    (sizeof fixed_start_env / sizeof fixed_start_env[0] \
     + sizeof fixed_start_allowed / sizeof fixed_start_allowed[0])

static char *fixed_start_argv[FIXED_START_MAX_ARGS + 1];
static int fixed_start_argc;
static char *fixed_start_environ[FIXED_START_ENV_SLOTS];
static char fixed_start_space[FIXED_START_SPACE];
static fixed_start_main_fn fixed_start_program_main;

static void fixed_start_refuse(const char *reason)
{
    static const char head[] = "fixed start: the start is refused: ";
    char line[160];
    size_t n = sizeof head - 1;
    size_t r = strlen(reason);
    ssize_t w;

    memcpy(line, head, n);
    if (r > sizeof line - n - 1)
        r = sizeof line - n - 1;
    memcpy(line + n, reason, r);
    n += r;
    line[n++] = '\n';
    w = write(2, line, n);
    (void)w;
    _exit(FIXED_START_EXIT_REFUSED);
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
