#!/usr/bin/env python3
"""Check the provenance/envelope of AL-05's four Z3-replayed TypeId artifacts.

This is an independent evidence-integrity gate, *not* a second SMT checker:
the Rust Alive-lite Z3 test reopens each file and independently reproves the
real native TypeId CSE rewrite from canonical text-v3 snapshots.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

MAGIC = "AL05_INDUCTION_REPLAY 1"
CONTRACT = "al05-closed-i32-cse-loop-v1"
IR = "compiler-ir-native-module-3"
PASS = "typed-cse"
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_BYTES = 256 * 1024
MAX_LINES = 1024


def verify_file(path: Path, seed: int, source_sha: str) -> None:
    """Reject incorrect provenance, counts, changed-output evidence or envelope."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"invalid replay path/size: {path}")
    raw = path.read_bytes()
    if not raw.endswith(b"\n") or b"\r" in raw or b"\x00" in raw:
        raise ValueError(f"invalid replay bytes: {path}")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"non-UTF8 replay: {path}") from error
    at = 0

    def take() -> str:
        """Return exactly one record line or fail on truncation."""
        nonlocal at
        if at >= len(lines):
            raise ValueError(f"truncated replay {path}")
        line = lines[at]
        at += 1
        return line

    for expected in (
        MAGIC, f"contract {CONTRACT}", f"ir {IR}", f"pass {PASS}",
        f"seed {seed}", f"compiler_sha {source_sha}",
    ):
        if take() != expected:
            raise ValueError(f"unexpected AL-05 header for {path}: {expected}")

    modules = []
    for name in ("before", "after"):
        prefix = f"{name}_lines "
        length = take()
        if not length.startswith(prefix):
            raise ValueError(f"missing AL-05 {name} length in {path}")
        n = length[len(prefix):]
        if not n.isascii() or not n.isdecimal() or n != str(int(n)):
            raise ValueError(f"invalid AL-05 {name} count in {path}")
        count = int(n)
        if not 2 <= count <= MAX_LINES or at + count > len(lines):
            raise ValueError(f"invalid AL-05 {name} bounds in {path}")
        body = lines[at : at + count]
        at += count
        if body[0] != "compiler-ir-native-module 3" or body[-1] != "endmodule":
            raise ValueError(f"invalid TypeId text-v3 boundary in {path}")
        if body.count(f"function al05_inductive_{seed}") != 1:
            raise ValueError(f"incorrect AL-05 function identity in {path}")
        modules.append(tuple(body))
    if modules[0] == modules[1]:
        raise ValueError(f"unchanged source/after cannot count as AL-05 proof: {path}")
    if take() != "endreplay" or at != len(lines):
        raise ValueError(f"AL-05 replay has extra/truncated lines: {path}")


def validate(root: Path, source_sha: str) -> int:
    """Require exactly four independently source-SHA-tagged replay files."""
    if SOURCE_SHA.fullmatch(source_sha) is None:
        raise ValueError("AL-05 evidence must use an immutable lowercase compiler SHA")
    expected = {f"al05-induction-{i}.replay" for i in range(4)}
    if not root.is_dir() or {path.name for path in root.iterdir()} != expected:
        raise ValueError("AL-05 replay directory must contain exactly seeds 0..3")
    for seed in range(4):
        verify_file(root / f"al05-induction-{seed}.replay", seed, source_sha)
    return len(expected)


def main() -> None:
    """Validate CLI arguments and emit a machine-readable acceptance record."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    count = validate(args.dir, args.source_sha)
    print(
        "AL05_REPLAY_ARTIFACTS "
        f"files={count} source_sha={args.source_sha} "
        f"contract={CONTRACT} ir={IR} verdict=PASS"
    )


if __name__ == "__main__":
    main()
