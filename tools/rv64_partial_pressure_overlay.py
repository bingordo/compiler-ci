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
MIXED_FIXTURE = r'''
/// Eight-input TypeId pressure workload with paired materialized and
/// rematerializable consumers. Configurations alter constant legality and
/// live-range ordering without changing compiler target or optimization.
pub fn partial_mixed_pressure_module(count: u32, mode: u8) -> TypedSsaModule {
    assert!(count > 0);
    assert!(mode <= 2);
    let mut insts = Vec::new();
    let mut outputs = Vec::new();
    let mut next = 8_u32;
    let mut constants = Vec::new();

    // Mode 0: all signed-i12 and long-lived. Mode 1: a controlled
    // out-of-range fraction. Mode 2: interleaved definitions and users.
    // The source workload size is a benchmark parameter, not a compiler cap.
    if mode != 2 {
        for i in 0..count {
            let signed = match i % 4 {
                0 => -2048 + i64::from(i),
                1 => 2047 - i64::from(i),
                2 => -17 - i64::from(i),
                _ => 37 + i64::from(i),
            };
            let value = if mode == 1 && i % 4 == 0 {
                5000 + i64::from(i)
            } else { signed };
            insts.push(i64_inst(next, Op::ConstI64(value)));
            constants.push(ValueId(next));
            next += 1;
        }
    }

    for i in 0..count {
        let constant = if mode == 2 {
            let value = if i % 2 == 0 { 31 + i64::from(i) }
                        else { -31 - i64::from(i) };
            insts.push(i64_inst(next, Op::ConstI64(value)));
            let id = ValueId(next);
            next += 1;
            id
        } else { constants[i as usize] };
        let kind = match i % 4 {
            0 => IntBinOp::Add,
            1 => IntBinOp::And,
            2 => IntBinOp::Or,
            _ => IntBinOp::Xor,
        };
        let arg = ValueId(i % 8);
        let foldable = next;
        insts.push(i64_inst(foldable, Op::IntBin {
            kind, bits: 64, lhs: arg, rhs: constant,
        }));
        next += 1;
        // Subtract the same constant in a register-register operation
        // that cannot be rewritten into RV64I SUB-immediate: its SSA use
        // deliberately keeps the constant definition live.
        let retained = next;
        insts.push(i64_inst(retained, Op::IntBin {
            kind: IntBinOp::Sub, bits: 64,
            lhs: constant, rhs: ValueId((i + 3) % 8),
        }));
        next += 1;
        outputs.push(ValueId(foldable));
        outputs.push(ValueId(retained));
    }

    // All outputs are live until the final fan-in, independent of the
    // shape of the constant definition/use lifetime.
    let mut result = ValueId(0);
    for arg in 1..8 {
        insts.push(i64_inst(next, Op::IntBin {
            kind: IntBinOp::Add, bits: 64,
            lhs: result, rhs: ValueId(arg),
        }));
        result = ValueId(next);
        next += 1;
    }
    for value in outputs {
        insts.push(i64_inst(next, Op::IntBin {
            kind: IntBinOp::Add, bits: 64, lhs: result, rhs: value,
        }));
        result = ValueId(next);
        next += 1;
    }
    let mode_name = match mode {
        0 => "mix",
        1 => "sparse",
        2 => "interleave",
        _ => unreachable!(),
    };
    module_with_body(
        &format!("rv64_partial_{mode_name}_{count}"), 8, insts, result,
    )
}
'''
EXTRA_CASES = '''        "partial16" => fixtures::partial_pressure_module(16),
        "partial32" => fixtures::partial_pressure_module(32),
        "mix8" => fixtures::partial_mixed_pressure_module(8, 0),
        "mix16" => fixtures::partial_mixed_pressure_module(16, 0),
        "mix32" => fixtures::partial_mixed_pressure_module(32, 0),
        "mix64" => fixtures::partial_mixed_pressure_module(64, 0),
        "sparse16" => fixtures::partial_mixed_pressure_module(16, 1),
        "sparse32" => fixtures::partial_mixed_pressure_module(32, 1),
        "sparse64" => fixtures::partial_mixed_pressure_module(64, 1),
        "interleave16" => fixtures::partial_mixed_pressure_module(16, 2),
        "interleave32" => fixtures::partial_mixed_pressure_module(32, 2),
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
        assert "pub fn partial_mixed_pressure_module(" not in f
        assert e.count(ANCHOR) == 1, (root, e.count(ANCHOR))
        inputs[str(root)] = (f, e)
    assert len({s[0] for s in inputs.values()}) == 1, "fixtures differ before overlay"
    assert len({s[1] for s in inputs.values()}) == 1, "examples differ before overlay"
    for root in roots:
        fixture = root / "crates/backend-riscv64/tests/fixtures/mod.rs"
        example = root / "crates/backend-riscv64/examples/rv64_leaf.rs"
        f, e = inputs[str(root)]
        updated_f = f.rstrip() + "\n\n" + FIXTURE + "\n\n" + MIXED_FIXTURE
        updated_e = e.replace(ANCHOR, EXTRA_CASES + ANCHOR)
        fixture.write_text(updated_f)
        example.write_text(updated_e)
        digest = hashlib.sha256((updated_f + updated_e).encode()).hexdigest()
        print(f"overlay {root}: sha256={digest}")

if __name__ == "__main__":
    main()
