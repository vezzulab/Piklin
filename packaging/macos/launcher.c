/* Piklin.app's executable: starts Piklin with the runtime for this Mac.
 *
 * Built as one universal binary. Each half knows its own processor and picks
 * the runtime built for it - Contents/Resources/runtime-arm64 or
 * runtime-x86_64 - so an Apple Silicon Mac never runs Piklin translated and
 * an Intel Mac needs nothing installed. Both halves run the same
 * Contents/Resources/boot.py in the Python framework the app carries.
 */
#include <Python.h>

#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__arm64__)
#define RUNTIME "runtime-arm64"
#else
#define RUNTIME "runtime-x86_64"
#endif

static void strip_last(char *path)
{
    char *slash = strrchr(path, '/');
    if (slash)
        *slash = '\0';
}

int main(int argc, char **argv)
{
    char exe[PATH_MAX], contents[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, contents) == NULL) {
        fprintf(stderr, "Piklin: cannot find where the app is\n");
        return 1;
    }
    strip_last(contents);                       /* .../Contents/MacOS */
    strip_last(contents);                       /* .../Contents */

    char home[PATH_MAX], runtime[PATH_MAX], boot[PATH_MAX];
    snprintf(home, sizeof home, "%s/Frameworks/Python.framework/Versions/3.14", contents);
    snprintf(runtime, sizeof runtime, "%s/Resources/" RUNTIME, contents);
    snprintf(boot, sizeof boot, "%s/Resources/boot.py", contents);

    /* Only what the app carries: no Python settings or packages of the Mac's own. */
    setenv("PIKLIN_BUNDLE_CONTENTS", contents, 1);
    setenv("PIKLIN_RUNTIME", runtime, 1);
    setenv("PYTHONHOME", home, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONSTARTUP");

    /* Piklin reopens itself the way it does from a Python interpreter,
     * "-s -m piklin.app --library ..."; those first three are Python's. */
    int first = 1;
    if (argc >= 4 && strcmp(argv[1], "-s") == 0 && strcmp(argv[2], "-m") == 0
            && strcmp(argv[3], "piklin.app") == 0)
        first = 4;

    char **args = calloc((size_t)argc + 2, sizeof *args);
    if (args == NULL)
        return 1;
    int n = 0;
    args[n++] = argv[0];
    args[n++] = boot;
    for (int i = first; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0)  /* added by older Finders */
            continue;
        args[n++] = argv[i];
    }
    args[n] = NULL;
    return Py_BytesMain(n, args);
}
