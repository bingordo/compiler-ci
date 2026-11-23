#!/usr/bin/env python3
"""Overlay the same pressure-heavy TypeId partial-remat fixture on both pins.

The fixture deliberately combines each signed-i12 constant's foldable ADD
consumer with a nonfoldable SUB consumer, so the original constant must
remain live. This is benchmark-only code and changes neither compiler.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

FIXTURE = r'''
/// Source-owned high-pressure partial-rematerialization benchmark.
///
/// Each constant has an ADD (eligible for immediate folding) and SUB
/// (still needs the source register). Defining constants before both
/// groups keeps all of them live across the first uses. Their paired
/// results sum to n*(n+1) for any i64 input.
pub fn partial_pressure_module(count: u32) -> TypedSsaModule {
    assert!(count >= 1);
    let mut instructions = Vec::new();
    for i in 0..count {
        instructions.push(i64_inst(1 + i, Op::ConstI64(i64::from(i + 1))));
    }
    for i in 0..count {
        instructions.push(i64_inst(1 + count + i, Op::IntBin {
            kind: IntBinOp::Add, bits: 64,
            lhs: ValueId(0), rhs: ValueId(1 + i),
        }));
    }
    for i in 0..count {
        instructions.push(i64_inst(1 + 2 * count + i, Op::IntBin {
            kind: IntBinOp::Sub, bits: 64,
            lhs: ValueId(1 + i), rhs: ValueId(0),
        }));
    }
    let mut result = ValueId(count + 1);
    let mut next = 3 * count + 1;
    for value in (count + 2)..=(3 * count) {
        instructions.push(i64_inst(next, Op::IntBin {
            kind: IntBinOp::Add, bits: 64,
            lhs: result, rhs: ValueId(value),
        }));
        result = ValueId(next);
        next += 1;
    }
    module_with_body(
        &format!("rv64_partial_pressure_{count}"), 1, instructions, result,
    )
}
'''
EXTRA_CASES = '''        "partial16" => fixtures::partial_pressure_module(16),
        "partial32" => fixtures::partial_pressure_module(32),
'''
ANCHOR = '        "spillsum" => fixtures::spill_sum_module(48),'

def main() -> None:
    roots = [Path(s) for s in sys.argv[1:]]
    assert roots, "pass checkout roots"
    inputs = {}
    for root in roots:
        fixture = root / "crates/backend-riscv64/tests/fixtures/mod.rs"
        example = root / "crates/backend-riscv64/examples/rv64_leaf.rs"
        f, e = fixture.read_text(), example.read_text()
        assert "pub fn partial_pressure_module(" not in f
        assert e.count(ANCHOR) == 1, (root, e.count(ANCHOR))
        inputs[str(root)] = (f, e)
    assert len({s[0] for s in inputs.values()}) == 1, "fixtures differ before overlay"
    assert len({s[1] for s in inputs.values()}) == 1, "examples differ before overlay"
    for root in roots:
        fixture = root / "crates/backend-riscv64/tests/fixtures/mod.rs"
        example = root / "crates/backend-riscv64/examples/rv64_leaf.rs"
        f, e = inputs[str(root)]
        updated_f = f.rstrip() + "\n\n" + FIXTURE
        updated_e = e.replace(ANCHOR, EXTRA_CASES + ANCHOR)
        fixture.write_text(updated_f)
        example.write_text(updated_e)
        digest = hashlib.sha256((updated_f + updated_e).encode()).hexdigest()
        print(f"overlay {root}: sha256={digest}")

if __name__ == "__main__":
    main()
