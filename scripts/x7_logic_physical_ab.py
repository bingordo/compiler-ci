#!/usr/bin/env python3
"""Real x86 TypeId X7 A/B: default vs gated generated AND/OR/XOR rules.

Run from the exact-source compiler checkout. Report physical function .text
and frame-memory references for BOTH direct ELF and GAS, without assuming
pre-RA SSA operation savings translate into native-code savings.
"""
import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

FUNCTIONS = (
    "and32a", "and32b", "or32", "xor32",
    "and64a", "and64b", "or64", "xor64", "control",
)


def run(argv):
    proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(
            f"{argv!r} returned {proc.returncode}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc.stdout


def fixture():
    """Use exactly the same checked C source as the executable GCC oracle."""
    test = Path("crates/driver/tests/x7_production_generated_logic.rs").read_text()
    match = re.search(r'^const SOURCE:&str=r#"\n(.*?)\n"#;', test, re.M | re.S)
    if not match:
        raise RuntimeError("source fixture missing or changed format")
    source = match.group(1) + "\n"
    for function in FUNCTIONS:
        if not re.search(r'\b(?:int|long)\s+' + function + r'\s*\(', source):
            raise RuntimeError(f"missing expected fixture function {function}")
    return source


def emit(compiler, source, output_dir, opt, mode, pic, frame):
    filename = f"{opt[1:]}-{mode}-{int(pic)}-{int(frame)}"
    out = output_dir / f"{filename}.{'o' if mode == 'obj' else 's'}"
    command = [
        str(compiler), str(source), opt, "--language", "mini-c",
        "--target", "x86_64", "--emit", mode, "-o", str(out),
    ]
    if pic:
        command.append("--pic")
    if frame:
        command.append("--frame-pointer")
    run(command)
    if mode == "obj":
        return out
    assembled = output_dir / f"{filename}-gas.o"
    run(["cc", "-c", str(out), "-o", str(assembled)])
    return assembled


def function_sizes(obj):
    # Decimal POSIX nm syntax: "symbol T address size".
    result = {}
    for line in run(["nm", "--defined-only", "--format=posix",
                     "--print-size", "--radix=d", str(obj)]).splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] in FUNCTIONS and parts[1] in ("T", "t"):
            result[parts[0]] = int(parts[3])
    missing = set(FUNCTIONS) - result.keys()
    if missing or any(result[name] <= 0 for name in FUNCTIONS):
        raise RuntimeError(f"missing/zero ELF function sizes {missing}: {result}")
    return result


def text_bytes(obj):
    # .text.* is included: direct ELF and GNU assembler can split sections.
    rows = (line.split() for line in run(["size", "-A", "-d", str(obj)]).splitlines())
    return sum(int(row[1]) for row in rows
               if len(row) >= 2 and row[0].startswith(".text"))


def stack_refs(obj):
    # Operand refs to %rsp/%rbp are *not* a precise measure of RA spills.
    # They are a conservative observable proxy for investigating regressions.
    disasm = run(["objdump", "-d", str(obj)])
    return len(re.findall(r'(?:-?0x[0-9a-f]+)?\(%r(?:sp|bp)\)', disasm))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline.resolve(strict=True)
    candidate = args.candidate.resolve(strict=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="x7-native-ab-") as temp:
        root = Path(temp)
        source = root / "logic.c"
        source.write_text(fixture())
        for opt in ("-O0", "-O2"):
            for mode in ("obj", "asm"):
                for pic in (False, True):
                    for frame in (False, True):
                        before_dir, after_dir = root / "baseline", root / "candidate"
                        before_dir.mkdir(exist_ok=True)
                        after_dir.mkdir(exist_ok=True)
                        before = emit(baseline, source, before_dir, opt, mode, pic, frame)
                        after = emit(candidate, source, after_dir, opt, mode, pic, frame)
                        a, b = function_sizes(before), function_sizes(after)
                        if opt == "-O2":
                            for function in ("and32a", "and64a"):
                                if b[function] >= a[function]:
                                    raise RuntimeError(
                                        f"IP11 surviving rule failed to reduce {function} "
                                        f"physical ELF bytes: {opt}/{mode}/pic={pic}/"
                                        f"frame={frame} before={a[function]} after={b[function]}"
                                    )
                        if a["control"] != b["control"]:
                            raise RuntimeError(
                                f"nonmatching control changed under feature: "
                                f"{opt}/{mode}/pic={pic}/frame={frame}: "
                                f"{a['control']} -> {b['control']} bytes"
                            )
                        row = {
                            "opt": opt, "mode": mode, "pic": pic, "frame": frame,
                            "before_text": text_bytes(before),
                            "after_text": text_bytes(after),
                            "before_functions": sum(a.values()),
                            "after_functions": sum(b.values()),
                            "before_frame_mem_refs": stack_refs(before),
                            "after_frame_mem_refs": stack_refs(after),
                            "function_delta_bytes": {name: b[name] - a[name]
                                                     for name in FUNCTIONS},
                        }
                        rows.append(row)
                        print("X7_AB " + json.dumps(row, sort_keys=True), flush=True)
    winners = [r for r in rows if r["after_functions"] < r["before_functions"]]
    losers = [r for r in rows if r["after_functions"] > r["before_functions"]]
    print("X7_AB_SUMMARY " + json.dumps({
        "comparisons": len(rows),
        "improved": len(winners),
        "regressed": len(losers),
        "unchanged": len(rows) - len(winners) - len(losers),
        "note": "frame_mem_refs are not verified RA spills; no default enablement based only on this fixture",
    }, sort_keys=True))
    assert len(rows) == 16


if __name__ == "__main__":
    main()
