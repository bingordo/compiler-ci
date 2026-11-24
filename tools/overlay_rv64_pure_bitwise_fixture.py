#!/usr/bin/env python3
"""Overlay identical TypeId pure-bitwise benchmark fixtures on exact compilers.

The compiler implementation remains unchanged at both source SHAs; only the
previous version receives the new input fixture, byte-for-byte matching the
candidate. Fail closed on fixture drift.
"""
from pathlib import Path
import sys

START = "/// Source-owned, two-arm TypeId pure bitwise tree with a shared producer."
END = "/// Two CFG edges use the constant as a PHI argument; the same constant"
ANCHOR = '        "addi_pure_shared" => fixtures::shared_pure_addi_expression_module(),'
CASES = (
    "pure_andi_shared",
    "pure_ori_shared",
    "pure_xori_shared",
)

def overlay(before: Path, after: Path) -> None:
    path = "crates/backend-riscv64/tests/fixtures/mod.rs"
    a, b = before / path, after / path
    old, new = a.read_text(), b.read_text()
    assert old.count(END) == 1 and START not in old
    assert new.count(START) == 1 and new.count(END) == 1
    snippet = START + new.split(START, 1)[1].split(END, 1)[0]
    a.write_text(old.replace(END, snippet + END, 1))
    assert a.read_bytes() == b.read_bytes(), "TypeId fixture drift"

    path = "crates/backend-riscv64/examples/rv64_leaf.rs"
    a, b = before / path, after / path
    old, new = a.read_text(), b.read_text()
    assert old.count(ANCHOR) == 1 and new.count(ANCHOR) == 1
    for name in CASES:
        assert f'"{name}" =>' not in old
        assert new.count(f'"{name}" =>') == 1
    inserted = new.split(ANCHOR, 1)[1].split(
        '        "addi_phi" =>', 1)[0]
    # The baseline already has a newline after ANCHOR; avoid inserting
    # the trailing source newline twice when overlaying the candidate.
    a.write_text(old.replace(ANCHOR, ANCHOR + inserted.rstrip("\\n"), 1))
    assert a.read_bytes() == b.read_bytes(), "example source drift"
    print("Both exact-commit compilers consume byte-identical TypeId source.")

if __name__ == "__main__":
    assert len(sys.argv) == 3
    overlay(Path(sys.argv[1]), Path(sys.argv[2]))
