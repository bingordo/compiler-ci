#!/usr/bin/env python3
"""Reproducible *real C* TypeId generated-DAG A/B vs independent GCC.

This is an external compiler-ci evidence harness. It never changes compiler
production defaults. CPU/wall timings are observational, not pass/fail gates
on noisy GitHub-hosted machines. Every successful source is compiled into
two independent native objects and executed against GCC -fwrapv.
"""
import argparse
import hashlib
import json
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SEED = 20261120
LEAVES = ("x", "y", "0", "1", "-1", "2", "17", "-31")
OPS = ("+", "-", "*", "&", "|", "^")
EDGE_VALUES = (
    "-2147483647-1", "-2147483647", "-1073741824", "-65536",
    "-123", "-31", "-3", "-1", "0", "1", "2", "5", "17", "99",
    "12345", "65535", "1073741824", "2147483647",
)
HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
extern int kernel(int,int);
static int (*volatile function)(int,int) = kernel;
int main(void) {
  static const int values[] = {EDGE_VALUES};
  uint64_t h = 1469598103934665603ULL;
  for (unsigned i=0; i<sizeof(values)/sizeof(values[0]); ++i)
    for (unsigned j=0; j<sizeof(values)/sizeof(values[0]); ++j) {
      uint32_t z = (uint32_t)function(values[i],values[j]);
      h = (h ^ z) * 1099511628211ULL;
    }
  uint32_t seed = 0x93b1ac07u;
  for (unsigned i=0; i<512; ++i) {
    seed = seed*1664525u+1013904223u;
    int x = (int32_t)seed;
    seed = seed*1664525u+1013904223u;
    int y = (int32_t)seed;
    uint32_t z = (uint32_t)function(x,y);
    h = (h ^ z) * 1099511628211ULL;
  }
  printf("%llu\n", (unsigned long long)h);
  return 0;
}
""".replace("EDGE_VALUES", ",".join(EDGE_VALUES))


def generated_cases(compiler_root):
    sys.path.insert(0, str(compiler_root / "tools"))
    from source_compile_corpus import c_source
    authored = {
        "add_sub_neg": "x-(0-y)",
        "add_mul_neg": "x-(-1*y)",
        "neg_sub": "y-(0-x)",
        "neg_mul": "y-(-1*x)",
        "commute": "y-(-1*x)",
        "sub_add": "x-(y-(y+y))",
        "and_or": "x+y-(x&y)",
        "xor_add": "(x^y)+(x&y)",
        "mul_fold": "(x-y)*2+(y+x)",
        "sub_of_sub": "x-(y-(x+y))",
    }
    cases = {
        "expr_" + name: "int kernel(int x,int y){return " + expr + ";}\n"
        for name, expr in authored.items()
    }
    def expr(rng, depth):
        if depth == 0 or rng.randrange(7) == 0:
            return rng.choice(LEAVES)
        return "(" + expr(rng, depth - 1) + " " + rng.choice(OPS) + " " + expr(rng, depth - 1) + ")"
    for n in range(48):
        rng = random.Random(SEED + n)
        body = expr(rng, 3)
        cases[f"fuzz_{n:03}"] = f"int kernel(int x,int y){{return {body};}}\n"
    for family in ("array_loop", "branch_chain", "call_graph"):
        for n in (4, 8, 12, 24):
            cases[f"source_{family}_{n}"] = c_source(family, n)
    assert len(cases) == 70 and len(set(cases)) == 70
    return dict(sorted(cases.items()))


def run(command, timeout=45):
    started = time.perf_counter_ns()
    p = subprocess.run([str(x) for x in command], text=True, capture_output=True,
                       timeout=timeout, check=False)
    elapsed = time.perf_counter_ns() - started
    if p.returncode:
        raise RuntimeError(f"command failed exit={p.returncode}: {command!r}\n"
                           f"stdout={p.stdout[-1000:]}\nstderr={p.stderr[-1800:]}")
    return p.stdout.strip(), elapsed


def section_text_size(path):
    listing, _ = run(["objdump", "-h", path])
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] == ".text":
            return int(fields[2], 16)
    raise ValueError(f"missing .text section in {path}")


def measure_cases(compiler_root, default, optin, out_dir, only=None):
    compiler_root = Path(compiler_root).resolve()
    default, optin = Path(default).resolve(), Path(optin).resolve()
    out_dir = Path(out_dir).resolve()
    assert default.is_file() and optin.is_file()
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = generated_cases(compiler_root)
    if only is not None:
        if only not in cases:
            raise ValueError("unknown case: " + only)
        cases = {only: cases[only]}
    all_results = {}
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        host = temp / "harness.c"
        host.write_text(HARNESS)
        host_obj = temp / "harness.o"
        run(["gcc", "-O2", "-std=c11", "-fwrapv", "-c", host, "-o", host_obj])
        # Warm each separately to reduce cold-start and file-cache bias.
        warm = temp / "warm.c"
        warm.write_text("int kernel(int x,int y){return x-(0-y);}\n")
        for binary in (default, optin):
            run([binary, warm, "--language", "mini-c", "--ir", "typeid",
                 "--emit", "obj", "-o", temp / "warm.o"])
        for name, source in cases.items():
            src = temp / (name + ".c")
            src.write_text(source)
            gcc_obj = temp / (name + "_gcc.o")
            run(["gcc", "-O2", "-std=c11", "-fwrapv", "-fno-builtin",
                 "-c", src, "-o", gcc_obj])
            gcc_exe = temp / (name + "_gcc")
            run(["gcc", "-no-pie", host_obj, gcc_obj, "-o", gcc_exe])
            expected, _ = run([gcc_exe], timeout=20)
            # ABBA: two runs of each real compiler using the same source.
            timings = {"default": [], "optin": []}
            outputs = {}
            for label in ("default", "optin", "optin", "default"):
                driver = default if label == "default" else optin
                obj = temp / (name + "_" + label + ".o")
                _, ns = run([driver, src, "--language", "mini-c",
                             "--ir", "typeid", "--emit", "obj", "-o", obj],
                            timeout=70)
                timings[label].append(ns)
                sha = hashlib.sha256(obj.read_bytes()).hexdigest()
                if label in outputs and outputs[label]["object_sha256"] != sha:
                    raise RuntimeError(f"{name}: nondeterministic {label} ELF")
                outputs[label] = {
                    "object_sha256": sha,
                    "text_bytes": section_text_size(obj),
                    "object": obj,
                }
            for label, row in outputs.items():
                exe = temp / (name + "_" + label)
                run(["gcc", "-no-pie", host_obj, row["object"], "-o", exe])
                actual, _ = run([exe], timeout=20)
                if actual != expected:
                    raise RuntimeError(f"{name}/{label}: mismatch with GCC "
                                       f"expected={expected!r}, got={actual!r}")
                del row["object"]
            before = outputs["default"]["text_bytes"]
            after = outputs["optin"]["text_bytes"]
            delta = before - after
            summary = {
                "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "source_bytes": len(src.read_bytes()),
                "default": outputs["default"],
                "optin": outputs["optin"],
                "saved_text_bytes": delta,
                "gcc_native_execution_equal": True,
                "compile_ns_abba": timings,
                "median_compile_ns_default": int(statistics.median(timings["default"])),
                "median_compile_ns_optin": int(statistics.median(timings["optin"])),
            }
            all_results[name] = summary
            print("X7_WIDE_CASE", name, "saved", delta, "default", before,
                  "optin", after, flush=True)
    saved = sum(row["saved_text_bytes"] for row in all_results.values())
    reductions = sum(row["saved_text_bytes"] > 0 for row in all_results.values())
    increases = sum(row["saved_text_bytes"] < 0 for row in all_results.values())
    timing_default = sum(row["median_compile_ns_default"] for row in all_results.values())
    timing_optin = sum(row["median_compile_ns_optin"] for row in all_results.values())
    report = {
        "schema_version": 1,
        "kind": "actual_typeid_generated_dag_optin_wide_gcc_native_differential",
        "source_seed": SEED,
        "cases": all_results,
        "source_count": len(all_results),
        "gcc_reference": "gcc -O2 -std=c11 -fwrapv -fno-builtin",
        "reference_compiled_and_linked_natively": True,
        "native_gcc_agreement": True,
        "source_proof_or_new_isa_admission_changed": False,
        "default_feature_enabled": False,
        "text_saved_bytes": saved,
        "text_reductions": reductions,
        "text_increases": increases,
        "text_unchanged": len(all_results) - reductions - increases,
        "sum_case_median_compile_ns_default": timing_default,
        "sum_case_median_compile_ns_optin": timing_optin,
        "timing_interpretation": "4-process per-case ABBA diagnostic; shared runner noise; not a performance acceptance claim",
    }
    (out_dir / "wide-diff.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print("X7_WIDE_TOTAL", json.dumps({
        k: report[k] for k in (
            "source_count", "text_saved_bytes", "text_reductions",
            "text_increases", "text_unchanged",
            "sum_case_median_compile_ns_default",
            "sum_case_median_compile_ns_optin",
        )
    }, sort_keys=True), flush=True)
    # Check both sentinel source-proven ADD cases materially improve;
    # never claim randomized fuzz successes if no production rewrite ran.
    if only is None:
        for name in ("expr_add_sub_neg", "expr_add_mul_neg"):
            if all_results[name]["saved_text_bytes"] <= 0:
                raise RuntimeError(f"{name}: generated DAG code-size benefit missing")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--compiler-root", type=Path, required=True)
    p.add_argument("--default", type=Path, required=True)
    p.add_argument("--optin", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--only")
    args = p.parse_args()
    measure_cases(args.compiler_root, args.default, args.optin, args.out, args.only)


if __name__ == "__main__":
    main()
