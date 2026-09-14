#!/usr/bin/env python3
"""Make every library inside Piklin.app find its dependencies inside the app.

The libraries are built into a prefix on the build machine, and the Python
framework expects to live in /Library/Frameworks; each one names its
dependencies by those absolute paths. This rewrites every such path to one
relative to the app's executable, then checks that nothing outside the app
and macOS itself is referred to.

    relocate.py CONTENTS FROM=TO [FROM=TO ...]

    FROM  an absolute directory the binaries refer to
    TO    what it becomes, e.g. @executable_path/../Resources/runtime-arm64/lib

Binaries changed here lose their signature; build-macos.sh signs the app
afterwards.
"""
from __future__ import annotations

import os
import subprocess
import sys

MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",     # 64-bit, either byte order
               b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}     # universal
SYSTEM = ("/usr/lib/", "/System/")


def is_macho(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) in MACHO_MAGIC
    except OSError:
        return False


def _tool(*args: str) -> list[str]:
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def dependencies(path: str) -> list[str]:
    """The libraries ``path`` loads (for a universal file, every slice's)."""
    seen: list[str] = []
    for line in _tool("otool", "-L", path):
        if line.startswith("\t"):
            dep = line.strip().rsplit(" (compatibility", 1)[0]
            if dep not in seen:
                seen.append(dep)
    return seen


def install_id(path: str) -> str | None:
    for line in _tool("otool", "-D", path)[1:]:
        line = line.strip()
        if line and not line.endswith(":"):
            return line
    return None


def rewrite(path: str, mapping: list[tuple[str, str]]) -> str | None:
    for old, new in mapping:
        if path.startswith(old):
            return new + path[len(old):]
    return None


def machos(contents: str):
    for root, _dirs, files in os.walk(contents):
        for name in files:
            p = os.path.join(root, name)
            if not os.path.islink(p) and is_macho(p):
                yield p


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    contents = argv[0]
    mapping = []
    for pair in argv[1:]:
        old, _, new = pair.partition("=")
        mapping.append((old.rstrip("/") + "/", new.rstrip("/") + "/"))
    # Longest first, so a nested directory wins over the one containing it.
    mapping.sort(key=lambda m: len(m[0]), reverse=True)

    changed = 0
    for path in machos(contents):
        args: list[str] = []
        ident = install_id(path)
        for dep in dependencies(path):
            if dep == ident:
                continue
            new = rewrite(dep, mapping)
            if new:
                args += ["-change", dep, new]
        if ident:
            new = rewrite(ident, mapping)
            if new:
                args += ["-id", new]
        if args:
            os.chmod(path, os.stat(path).st_mode | 0o200)
            subprocess.run(["install_name_tool", *args, path], check=True,
                           capture_output=True)
            changed += 1

    stray = []
    for path in machos(contents):
        ident = install_id(path)
        for dep in dependencies(path):
            if dep == ident or dep.startswith("@") or dep.startswith(SYSTEM):
                continue
            stray.append(f"{os.path.relpath(path, contents)} -> {dep}")
    print(f"relocated {changed} binaries")
    if stray:
        print("still refers to files outside the app:", *stray[:40], sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
