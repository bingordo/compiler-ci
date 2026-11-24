#!/usr/bin/env python3
"""Reproduce an identical TypeId cross-block fixture at exact A/B commits.

Only the baseline's input fixture and example dispatch are overlaid; neither
compiler implementation is changed. Byte comparison must pass before running
the independent code-generation size comparison.
"""
from pathlib import Path
import sys

START = "/// A source TypeId CFG with one dominating pure expression and two"
END = "/// Two CFG edges use the constant as a PHI argument; the same constant"
EXAMPLE_ANCHOR = (
    '        "addi_pure_shared" => fixtures::shared_pure_addi_expression_module(),\n'
)
EXAMPLE_ADDITION = (
    '        "pure_addi_crossblock" => fixtures::cross_block_pure_addi_module(),\n'
)

def overlay(old: Path, new: Path) -> None:
    path = "crates/backend-riscv64/tests/fixtures/mod.rs"
    before = (old / path).read_text()
    after = (new / path).read_text()
    assert START not in before and before.count(END) == 1
    assert after.count(START) == 1 and after.count(END) == 1
    a, z = after.index(START), after.index(END, after.index(START))
    (old / path).write_text(before.replace(END, after[a:z] + END, 1))
    assert (old / path).read_bytes() == (new / path).read_bytes(), "fixture mismatch"

    path = "crates/backend-riscv64/examples/rv64_leaf.rs"
    before = (old / path).read_text()
    after = (new / path).read_text()
    assert before.count(EXAMPLE_ANCHOR) == 1
    assert EXAMPLE_ADDITION not in before
    (old / path).write_text(before.replace(
        EXAMPLE_ANCHOR, EXAMPLE_ANCHOR + EXAMPLE_ADDITION, 1))
    assert (old / path).read_bytes() == (new / path).read_bytes(), "example mismatch"
    print("Verified byte-identical before/after source-owned cross-block input.")

if __name__ == "__main__":
    assert len(sys.argv) == 3
    overlay(Path(sys.argv[1]), Path(sys.argv[2]))
