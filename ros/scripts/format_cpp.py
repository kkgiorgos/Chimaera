#!/usr/bin/env python3
"""Format selected C/C++ files or directories using their nearest .clang-format."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess


EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl"}
EXCLUDED = {"build", "install", "log", "logs", "devel", "node_modules"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report changes without writing")
    parser.add_argument("paths", nargs="+", type=Path, help="files or directories to format")
    args = parser.parse_args()
    formatter = shutil.which(os.environ.get("CLANG_FORMAT", "clang-format"))
    if formatter is None:
        parser.error("clang-format not found; install it or set CLANG_FORMAT")

    files = set()
    for path in args.paths:
        if path.is_file():
            if path.suffix not in EXTENSIONS:
                parser.error(f"unsupported C/C++ file: {path}")
            files.add(path.resolve())
        elif path.is_dir():
            for directory, subdirs, names in os.walk(path):
                subdirs[:] = [
                    name for name in subdirs
                    if not name.startswith(".") and name not in EXCLUDED
                ]
                for name in names:
                    candidate = Path(directory) / name
                    if candidate.suffix in EXTENSIONS and not candidate.is_symlink():
                        files.add(candidate.resolve())
        else:
            parser.error(f"path does not exist: {path}")

    if not files:
        parser.error("no C/C++ files found")
    options = ["--dry-run", "--Werror"] if args.check else ["-i"]
    failed = False
    for path in sorted(files):
        result = subprocess.run([formatter, "--style=file", *options, str(path)])
        failed |= result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
