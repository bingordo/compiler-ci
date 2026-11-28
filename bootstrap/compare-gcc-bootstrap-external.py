#!/usr/bin/env python3
"""Run the same external corpus on historical rustc and mrustc/GCC compiler binaries.

Only aggregate results and public generated-case IDs are published. A shared
rejection is not successful execution; exact binary parity is separate from
the independent GCC runtime oracle.
"""
import argparse
import hashlib
import os
from pathlib import Path
import random
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument("--host", type=Path, required=True)
p.add_argument("--bootstrap", type=Path, required=True)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--csmith-include", type=Path, required=True)
args = p.parse_args()
host, boot, root = args.host.resolve(), args.bootstrap.resolve(), args.source.resolve()

def run(cmd, seconds=45):
    try:
        result = subprocess.run(list(map(str, cmd)), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=seconds)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        return 124, b""

def compile_driver(driver, source, obj, ir=False):
    obj.unlink(missing_ok=True)
    front = ["--load-ir", source] if ir else [source, "--language", "mini-c"]
    rc, _ = run([driver, *front, "--target", "x86_64", "--emit", "obj", "-o", obj])
    if rc == 0 and (not obj.is_file() or obj.read_bytes()[:4] != b"\x7fELF"):
        return 125
    return rc

def compare(source, tmp, label, ir=False, harness=None, gcc_include=None):
    h, b, g = [tmp / (name + ".o") for name in ("host", "boot", "gcc")]
    hr, br = compile_driver(host, source, h, ir), compile_driver(boot, source, b, ir)
    if hr != br:
        print(f"{label} ACCEPTANCE_MISMATCH host={hr} boot={br}", flush=True)
        return "mismatch"
    if hr != 0:
        if hr >= 124 or hr < 0:
            print(f"{label} ABNORMAL_EXIT={hr}", flush=True)
            return "mismatch"
        return "shared_reject"
    if h.read_bytes() != b.read_bytes():
        print(f"{label} ELF_MISMATCH", flush=True)
        return "mismatch"
    if ir:
        return "elf_equal"
    # A third compiler is the execution oracle, not the host-built compiler.
    cc = ["gcc", "-std=gnu11", "-O2"]
    if gcc_include:
        cc += ["-I", gcc_include]
    if run([*cc, "-c", source, "-o", g])[0]:
        print(f"{label} GCC_COMPILE_FAILURE", flush=True)
        return "mismatch"
    results = []
    for obj in (g, h, b):
        exe = tmp / "program"
        exe.unlink(missing_ok=True)
        cmd = ["gcc", "-no-pie", obj]
        if harness:
            cmd.append(harness)
        if run([*cmd, "-o", exe])[0]:
            print(f"{label} LINK_FAILURE", flush=True)
            return "mismatch"
        results.append(run([exe], 10))
    if any(rc != 0 for rc, _ in results) or len(set(results)) != 1:
        print(f"{label} EXECUTION_MISMATCH exits={[r[0] for r in results]}", flush=True)
        return "mismatch"
    return "runtime_equal"

def summary(name, values):
    from collections import Counter
    counts = Counter(values)
    print(name + " " + " ".join(f"{key.upper()}={counts[key]}" for key in
          ("runtime_equal", "elf_equal", "shared_reject", "mismatch")), flush=True)
    return counts

with tempfile.TemporaryDirectory() as directory:
    tmp = Path(directory)
    # Existing textual IR regression fixtures, at the exact source revision.
    # Retain deterministic ordering and include both positive and negative cases.
    fixtures = sorted(path for path in root.rglob("*.ir") if
                      ".git" not in path.parts and "target" not in path.parts)
    # Evenly sample the entire corpus if it exceeds the bounded first run.
    if len(fixtures) > 128:
        fixtures = [fixtures[i * len(fixtures) // 128] for i in range(128)]
    ir_results = [compare(path, tmp, f"IR{i}", ir=True) for i, path in enumerate(fixtures)]
    print(f"IR_FIXTURES={len(fixtures)}", flush=True)
    ir = summary("IR", ir_results)

    # Generated scalar ABI matrix, including register and stack arguments.
    # Independent GCC executes the same source and a separate C caller.
    rng = random.Random(1548)
    abi_results = []
    for count in range(11):
        for seed in range(4):
            coeff = [rng.randrange(1, 10) for _ in range(count)]
            params = ", ".join(f"int a{i}" for i in range(count)) or "void"
            expr = " + ".join(f"a{i} * {c}" for i, c in enumerate(coeff)) or "17"
            src = tmp / "abi.c"
            src.write_text(f"int kernel({params}) {{ return {expr}; }}\n")
            h = tmp / "harness.c"
            calls = []
            for k in range(16):
                vals = ", ".join(str(rng.randrange(-100, 101)) for _ in range(count))
                calls.append(f'printf("%d\\n", kernel({vals}));')
            h.write_text("#include <stdio.h>\nextern int kernel(" + params +
                         ");\nint main(void){\n" + "\n".join(calls) + "\nreturn 0;\n}\n")
            abi_results.append(compare(src, tmp, f"ABI{count}_{seed}", harness=h))
    abi = summary("ABI", abi_results)

    # Actual unmodified Csmith programs. Preprocess with GCC using the installed
    # public runtime headers so both compiler builds receive identical input.
    csmith_results = []
    for seed in range(1, 9):
        rc, source = run(["csmith", "--seed", seed, "--max-funcs", "3",
                          "--max-block-depth", "2", "--no-pointers", "--no-unions",
                          "--no-volatiles", "--no-longlong", "--no-bitfields"], 20)
        if rc:
            print(f"CSMITH{seed} GENERATOR_EXIT={rc}", flush=True)
            csmith_results.append("mismatch")
            continue
        src = tmp / "csmith.c"
        src.write_bytes(source)
        pre = tmp / "csmith.i.c"
        rc, expanded = run(["gcc", "-E", "-P", "-I", args.csmith_include, src])
        if rc:
            print(f"CSMITH{seed} PREPROCESS_EXIT={rc}", flush=True)
            csmith_results.append("mismatch")
            continue
        pre.write_bytes(expanded)
        csmith_results.append(compare(pre, tmp, f"CSMITH{seed}"))
    cs = summary("CSMITH", csmith_results)
    # Whole-program Csmith coverage is explicitly measured, not assumed.
    # Shared rejects are reported; scalar ABI must show meaningful execution.
    if ir["mismatch"] or abi["mismatch"] or cs["mismatch"] or abi["runtime_equal"] < 12:
        raise SystemExit(1)
