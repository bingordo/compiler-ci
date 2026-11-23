#!/usr/bin/env python3
"""Compare source-pinned RV64 rematerialization code generation.

Inputs use byte-identical TypeId fixture sources before and after the
partial-rematerialization change. This reports exact static emitted .text
bytes, instruction counts, stack accesses and ALU/compare operand reads.
It does NOT measure latency or predict RV64 hardware performance.
"""
from __future__ import annotations
import collections
import json
from pathlib import Path
import re
import subprocess
import sys

CASES = (
    "addi_phi", "addi_shared", "slti32_neg", "sltiu32_neg",
    "bitwise_shared", "spillsum", "pressure16", "pressure48",
    "spillbranch", "branch", "same", "partial16", "partial32",
)
REG_OPS = {"add", "and", "or", "xor", "slt", "sltu"}
IMM_OPS = {"addi", "andi", "ori", "xori", "slti", "sltiu"}
DISASM = re.compile(r"^\s*[0-9a-f]+:\s+[0-9a-f]{4,8}\s+([a-z][.a-z0-9]*)\b(.*)$")

def run(*args: str) -> str:
    return subprocess.check_output(args, text=True)

def count(path: Path) -> dict:
    disasm = run("riscv64-linux-gnu-objdump", "-d", str(path))
    opcode = collections.Counter()
    stack_accesses = 0
    for line in disasm.splitlines():
        m = DISASM.match(line)
        if not m:
            continue
        mnemonic, operands = m.groups()
        opcode[mnemonic] += 1
        if mnemonic in {"ld", "lw", "sd", "sw"} and re.search(r"\(sp\)", operands):
            stack_accesses += 1
    assert opcode, (path, disasm[-1000:])
    raw_text = Path(str(path) + ".text")
    subprocess.run(["riscv64-linux-gnu-objcopy", "-O", "binary",
                    "-j", ".text", str(path), str(raw_text)], check=True)
    return {
        "text_bytes": raw_text.stat().st_size,
        "instructions": sum(opcode.values()),
        "stack_load_store_instructions": stack_accesses,
        "gp_read_sites_on_alu_compare": (
            2 * sum(opcode[k] for k in REG_OPS)
            + sum(opcode[k] for k in IMM_OPS)
        ),
        "register_alu_compare_forms": sum(opcode[k] for k in REG_OPS),
        "immediate_alu_compare_forms": sum(opcode[k] for k in IMM_OPS),
        "opcodes": dict(sorted(opcode.items())),
    }

def main() -> None:
    root = Path(sys.argv[1])
    results = {}
    for case in CASES:
        old, new = (count(root / mode / (case + ".o")) for mode in ("old", "new"))
        diff = {k: new[k] - old[k] for k in (
            "text_bytes", "instructions", "stack_load_store_instructions",
            "gp_read_sites_on_alu_compare", "register_alu_compare_forms",
            "immediate_alu_compare_forms",
        )}
        results[case] = {"old": old, "new": new, "delta": diff}
        print(f"{case:16} bytes={old['text_bytes']:4}->{new['text_bytes']:4}"
              f" insns={old['instructions']:3}->{new['instructions']:3}"
              f" stack_ops={old['stack_load_store_instructions']:3}->{new['stack_load_store_instructions']:3}"
              f" GP_alu_reads={old['gp_read_sites_on_alu_compare']:3}->{new['gp_read_sites_on_alu_compare']:3}"
              f" reg_forms={old['register_alu_compare_forms']}->{new['register_alu_compare_forms']}"
              f" imm_forms={old['immediate_alu_compare_forms']}->{new['immediate_alu_compare_forms']}")
    assert results["addi_phi"]["delta"]["gp_read_sites_on_alu_compare"] < 0, (
        "addi_phi did not exercise partial rematerialization",
        results["addi_phi"],
    )
    for kind in ("partial16", "partial32"):
        assert results[kind]["delta"]["gp_read_sites_on_alu_compare"] < 0, (
            f"{kind} did not exercise the intended high-pressure partial fold",
            results[kind],
        )
    out = {
        "method": "source-pinned same-TypeId-input RV64 code-generation comparison",
        "measurement_scope": "static object-code counts only; no target runtime measurement",
        "versions": {
            "old": "213831b84dd5546e3aab7ee578c25de61b3c6434",
            "new": "c1f89639e5e776d81bb4e4947b4431a6ec9cae5c",
        },
        "fixture_matrix": results,
    }
    output = root / "comparison.json"
    output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print("JSON:", output)

if __name__ == "__main__":
    main()
