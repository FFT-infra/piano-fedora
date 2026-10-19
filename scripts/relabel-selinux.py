#!/usr/bin/env python3
"""Apply SELinux labels from file_contexts to a mounted rootfs.

Mirrors setfiles(8): parse file_contexts, match each path, write the
security.selinux xattr. A literal match wins over a regex one; among
regexes the last match wins.
"""
import os
import re
import sys

ROOT = os.path.realpath(sys.argv[1])
CTX = sys.argv[2]

SKIP = {"dev", "proc", "sys", "run", "mnt", "media", "tmp"}

literals = {}
regexes = []
with open(CTX, encoding="utf-8", errors="surrogateescape") as fh:
    for raw in fh:
        line = raw.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern = parts[0]
        # file_contexts lines are "<regex> [file_type] <context>" where the
        # optional middle column is "--" for a normal file. setfiles takes
        # the last field as the context, so we do the same.
        ctx = parts[-1]
        if any(c in pattern for c in "^.[]*$\\+?()|{}"):
            try:
                regexes.append((re.compile(pattern + r"\Z"), ctx))
            except re.error:
                continue
        else:
            literals[pattern] = ctx

print(f"rules: literals={len(literals)} regexes={len(regexes)}")

applied = skipped = errors = 0

def label_for(path):
    if path in literals:
        return literals[path]
    ctx = None
    for rx, c in regexes:
        if rx.match(path):
            ctx = c
    return ctx

CLEAN = len(sys.argv) > 3 and sys.argv[3] == "--clean"

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP]
    for name in dirnames + filenames:
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, ROOT)
        abs_rel = "/" + rel
        ctx = label_for(abs_rel)
        if ctx is None:
            skipped += 1
            continue
        try:
            if CLEAN:
                try:
                    os.removexattr(full, "security.selinux", follow_symlinks=False)
                except OSError:
                    pass
            os.setxattr(full, "security.selinux",
                        f"{ctx}\x00".encode("ascii"), follow_symlinks=False)
            applied += 1
        except OSError as exc:
            errors += 1
            if errors <= 10:
                print(f"  ERR {rel}: {exc}")

print(f"applied={applied} skipped={skipped} errors={errors}")
