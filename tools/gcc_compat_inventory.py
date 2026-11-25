#!/usr/bin/env python3
"""Inventory original GCC compatibility source roles from a local pinned checkout.

Keep discovery separate from execution: directives and incomplete families
remain visible until a runner explicitly supports their semantics.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from gcc_compat_abi_pilot import GCC_REVISION, GCC_COMPAT_DIR


def inventory(root: Path, prefix: str) -> list[dict]:
    """Group original translation units and preserve test directive text."""
    groups = {}
    for path in sorted(root.glob("*")):
        match = re.fullmatch(r"(.+)_(main|x|y)\.(c|C)", path.name)
        if not match or not match[1].startswith(prefix):
            continue
        name, role, _ = match.groups()
        source = path.read_text(errors="replace")
        group = groups.setdefault(name, {"case": name, "roles": {}})
        if role in group["roles"]:
            raise ValueError(f"duplicate role: {name}/{role}")
        group["roles"][role] = {
            "file": path.name,
            "directive_lines": [line.strip() for line in source.splitlines()
                                if re.search(r"\bdg-[\w-]+", line)],
        }
    for group in groups.values():
        group["missing_roles"] = sorted({"main", "x", "y"} - group["roles"].keys())
        group["status"] = "INCOMPLETE" if group["missing_roles"] else "DISCOVERED"
    return list(groups.values())


def main() -> int:
    """Verify checkout identity and emit a reproducible discovery manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--prefix", default="struct-return-")
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(args.checkout), "rev-parse", "HEAD"], text=True).strip()
    if revision != GCC_REVISION:
        parser.error(f"expected GCC {GCC_REVISION}, got {revision}")
    root = args.checkout / GCC_COMPAT_DIR
    if not root.is_dir():
        parser.error(f"missing compatibility directory: {root}")
    cases = inventory(root, args.prefix)
    if not cases:
        parser.error("no matching compatibility families")
    print(json.dumps({"gcc_revision": revision, "cases": cases}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
