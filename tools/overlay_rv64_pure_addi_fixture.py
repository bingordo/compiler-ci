#!/usr/bin/env python3
"""Overlay the exact new TypeId shared-ADDI fixture on its unmodified parent.

This script changes *only* the baseline's benchmark input source; the two
compiler implementations remain exact pinned commits. It then byte-compares
all benchmark input files to prevent denominator drift in the A/B.
"""
from pathlib import Path
import sys

START = "/// Two consumers of the *same* pure x+17 SSA expression,"
END = "/// Two CFG edges use the constant as a PHI argument; the same constant"
OLD_EXAMPLE = '        "addi_shared" => fixtures::shared_add_imm_module(),'
NEW_EXAMPLE = '        "addi_pure_shared" => fixtures::shared_pure_addi_expression_module(),'


def overlay(old: Path, new: Path, path: str, start: str, end: str) -> None:
    prior = old / path
    reference = new / path
    original = prior.read_text()
    updated = reference.read_text()
    assert original.count(end) == 1
    assert updated.count(start) == 1 and updated.count(end) == 1
    snippet = start + updated.split(start, 1)[1].split(end, 1)[0]
    assert start not in original
    prior.write_text(original.replace(end, snippet + end, 1))
    assert prior.read_bytes() == reference.read_bytes(), f"input differs: {path}"


def main() -> None:
    old, new = (Path(p) for p in sys.argv[1:3])
    overlay(old, new, "crates/backend-riscv64/tests/fixtures/mod.rs", START, END)
    path = "crates/backend-riscv64/examples/rv64_leaf.rs"
    old_file, new_file = old / path, new / path
    original = old_file.read_text()
    assert original.count(OLD_EXAMPLE) == 1 and NEW_EXAMPLE not in original
    old_file.write_text(original.replace(
        OLD_EXAMPLE, OLD_EXAMPLE + "\n" + NEW_EXAMPLE, 1))
    assert old_file.read_bytes() == new_file.read_bytes(), f"input differs: {path}"
    print("Baseline and candidate now consume byte-identical TypeId source fixtures.")


if __name__ == "__main__":
    main()
