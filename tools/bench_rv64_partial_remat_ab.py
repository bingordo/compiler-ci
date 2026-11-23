#!/usr/bin/env python3
"""Compare source-pinned RV64 rematerialization code and QEMU runtime.

Input objects are produced from the *same* TypeId fixture sources by the
compiler versions immediately before and after the partial-remat change.
Instruction and stack-access counts are exact for the emitted .text; runtime
samples are exploratory QEMU-user host-time measurements, not CPU cycles.
"""
from __future__ import annotations
import collections
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

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

def runtime_samples(root: Path, kind: str, iterations: int) -> dict:
    # No arbitrary source-program cap: the iteration count controls only the
    # duration of this benchmark. It is not a compiler capability limit.
    cpu = min(os.sched_getaffinity(0))
    paths = {mode: root / mode / (kind + "_bench.elf") for mode in ("old", "new")}
    for mode, elf in paths.items():
        assert elf.is_file(), elf
        for _ in range(2):
            subprocess.run(["taskset", "-c", str(cpu), "qemu-riscv64",
                            str(elf)], check=True, timeout=25)
    order = ["old", "new", "new", "old"] * 7
    samples = {"old": [], "new": []}
    for mode in order:
        t0 = time.perf_counter_ns()
        subprocess.run(["taskset", "-c", str(cpu), "qemu-riscv64",
                        str(paths[mode])], check=True, timeout=25)
        samples[mode].append((time.perf_counter_ns() - t0) / 1e6)
    med = {k: statistics.median(v) for k, v in samples.items()}
    return {
        "benchmark": f"{kind} executed {iterations} times in the QEMU call loop",
        "cpu_affinity": cpu,
        "metric": "QEMU-user process host wall time, milliseconds",
        "samples_ms": samples,
        "median_ms": med,
        "candidate_vs_baseline_pct": 100 * (med["new"] / med["old"] - 1),
        "caution": "QEMU startup, dynamic translation and host scheduling included; not target hardware cycles",
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
    runtime = {
        "addi_phi": runtime_samples(root, "addi_phi", 200000),
        "partial32": runtime_samples(root, "partial32", 20000),
    }
    for kind, sample in runtime.items():
        print(f"{kind} QEMU-user median host ms: {sample['median_ms']}")
        print(f"{kind} candidate vs baseline %: {sample['candidate_vs_baseline_pct']:.2f}")
    out = {"method": "source-pinned same-runner old/new .text and QEMU ABBA",
           "versions": {"old": "213831b84dd5546e3aab7ee578c25de61b3c6434",
                        "new": "c1f89639e5e776d81bb4e4947b4431a6ec9cae5c"},
           "fixture_matrix": results, "qemu_microbenchmark": runtime}
    output = root / "comparison.json"
    output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print("JSON:", output)

if __name__ == "__main__":
    main()
