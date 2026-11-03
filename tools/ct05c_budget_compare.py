#!/usr/bin/env python3
"""CT-05c single-variable 512 vs 1024 if-conversion cap experiment.

Same-runner ABBA compilation of real checked Mini-C 48/72/96-branch programs
and a looped branch program. Compare whole-process time/RSS and ELF .text
size, and verify BOTH compiler outputs against independent GCC C semantics.
No timing or code-size CI threshold; this experiment never alters main.
"""
import argparse
import csv
import hashlib
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CASES = (("branches", 48, 1), ("branches", 72, 1),
         ("branches", 96, 1), ("loop", 24, 4))
VALUES = tuple(range(-30, 31))
FIELDS = ("case", "branches", "loops", "variant", "round", "order",
          "wall_ns", "user_s", "sys_s", "peak_rss_kb",
          "object_bytes", "text_bytes", "object_sha256", "source_sha256")


def source_c(branches, loops):
    lines = ["int kernel(int x) {", "  int total=0;", "  int tick=0;"]
    if loops > 1:
        lines.append(f"  while(tick<{loops}) {{")
    for k in range(branches):
        threshold = k % 13 - 6
        left, right = k % 5 + 1, k % 7 + 2
        lines.append(
            f"    if (x>{threshold}) {{ total=total+{left}; }} "
            f"else {{ total=total+{right}; }}"
        )
    if loops > 1:
        lines.extend(["    tick=tick+1;", "  }"])
    lines.extend(["  return total+tick;", "}", ""])
    return "\n".join(lines)


def oracle_host(branches, loops):
    return (
        '#include <stdio.h>\n'
        'extern int kernel(int);\n'
        'static int expected(int x) {\n'
        '  int total=0;\n'
        f'  for(int loop=0; loop<{loops}; ++loop) {{\n'
        f'    for(int k=0; k<{branches}; ++k) {{\n'
        '      const int threshold=k%13-6;\n'
        '      total += x>threshold ? (k%5+1) : (k%7+2);\n'
        '    }\n'
        '  }\n'
        f'  return total+{(loops if loops>1 else 0)};\n'
        '}\n'
        'int main(void) {\n'
        '  for(int x=-30; x<=30; ++x) {\n'
        '    int result=kernel(x), correct=expected(x);\n'
        '    if(result != correct) {'
        '      fprintf(stderr,"mismatch x=%d got=%d want=%d\\n",x,result,correct);\n'
        '      return 7;\n'
        '    }\n'
        '  }\n'
        '  volatile int checksum=0;\n'
        '  for(int i=0; i<30000; ++i) checksum += kernel(i%61-30);\n'
        '  if(checksum==0) return 8;\n'
        '  return 0;\n'
        '}\n'
    )


def checked(cmd, timeout=120, cwd=None):
    proc = subprocess.run([str(x) for x in cmd], text=True,
                          capture_output=True, timeout=timeout, cwd=cwd)
    if proc.returncode:
        raise RuntimeError(
            f"command failed: {cmd}, exit={proc.returncode}\n"
            f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-2500:]}"
        )
    return proc


def native_verify(source, host, folder, cc, binaries, one_compile):
    gcc_obj = folder / "gcc.o"
    checked([cc, "-std=c11", "-O2", "-c", source, "-o", gcc_obj])
    for name, obj in {"gcc": gcc_obj, **binaries}.items():
        exe = folder / f"{name}.exe"
        checked([cc, "-std=c11", "-O2", host, obj, "-o", exe])
        checked([exe], timeout=60)
    return gcc_obj


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--helpers", type=Path, required=True)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--cc", default="cc")
    args = p.parse_args()
    if not 3 <= args.rounds <= 10:
        p.error("rounds must be 3..10")
    sys.path.insert(0, str(args.helpers.resolve()))
    from source_cli_profile import one_compile, text_size
    paths = {"before": args.before.resolve(), "after": args.after.resolve()}
    for binary in paths.values():
        if not binary.is_file():
            raise FileNotFoundError(binary)
    out = args.results.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows, summaries = [], []
    with tempfile.TemporaryDirectory(prefix="ct05c-") as temp:
        timing = Path(temp) / "time.txt"
        for case, branches, loops in CASES:
            name = f"{case}_{branches}"
            folder = out / name
            folder.mkdir(exist_ok=True)
            source, host = folder / "source.c", folder / "oracle.c"
            source.write_text(source_c(branches, loops))
            host.write_text(oracle_host(branches, loops))
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            objects = {v: folder / f"{v}.o" for v in paths}
            expected_hashes = {}
            for variant in paths:
                sample = one_compile(paths[variant], source, objects[variant], timing)
                expected_hashes[variant] = sample["object_sha256"]
            gcc_obj = native_verify(source, host, folder, args.cc, objects, one_compile)
            gcc_text = text_size(gcc_obj)

            for round_index in range(args.rounds):
                order = ("before", "after", "after", "before")
                if round_index % 2:
                    order = tuple(reversed(order))
                for position, variant in enumerate(order):
                    metrics = one_compile(paths[variant], source, objects[variant], timing)
                    if metrics["object_sha256"] != expected_hashes[variant]:
                        raise AssertionError(f"unstable output {name} {variant}")
                    rows.append({
                        "case": case, "branches": branches, "loops": loops,
                        "variant": variant, "round": round_index,
                        "order": position, "source_sha256": source_hash,
                        **metrics,
                    })
            # Validate the *final* objects too; not just their warmup outputs.
            native_verify(source, host, folder, args.cc, objects, one_compile)
            def med(variant, key):
                return statistics.median(
                    row[key] for row in rows if row["case"] == case
                    and row["branches"] == branches and row["variant"] == variant
                )
            before_ms = med("before", "wall_ns") / 1e6
            after_ms = med("after", "wall_ns") / 1e6
            before_rss = med("before", "peak_rss_kb") / 1024
            after_rss = med("after", "peak_rss_kb") / 1024
            before_text = med("before", "text_bytes")
            after_text = med("after", "text_bytes")
            before_obj = med("before", "object_bytes")
            after_obj = med("after", "object_bytes")
            changed = expected_hashes["before"] != expected_hashes["after"]
            summaries.append(
                f"| {name} | {before_ms:.2f} | {after_ms:.2f} "
                f"| {before_ms/after_ms:.2f}x | {before_rss:.1f} | {after_rss:.1f} "
                f"| {before_text} | {after_text} | {gcc_text} "
                f"| {before_obj} | {after_obj} "
                f"| {'changed' if changed else 'same'} | pass |"
            )
            print(f"CT05C {name}: source SHA={source_hash} "
                  f"baseline {before_ms:.2f}ms, cap1024 {after_ms:.2f}ms, "
                  f"text {before_text}->{after_text}B, GCC .text {gcc_text}B, "
                  f"native execution PASSED", flush=True)

    raw = out / "samples.csv"
    with raw.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    report = [
        "# CT-05c: if-conversion 512 vs 1024 CFG block cap",
        "",
        "One compiler-production constant differs; PRE keeps the 512-block cap.",
        f"Five ABBA rounds per case; each compiler starts fresh per observation."
        if args.rounds == 5 else f"{args.rounds} ABBA rounds per case.",
        "Both variants build on a *single* Actions runner. Native objects are",
        "linked with an independent C oracle and executed for 61 integer inputs;",
        "GCC -O2 compiles the same C function as a further control.",
        "Each native executable additionally evaluates 30,000 changing inputs.",
        "No timing/size CI threshold. GCC .text size is a reference, not",
        "an apples-to-apples instruction-quality comparison.",
        "",
        "| Source | Baseline ms | 1024 ms | Speedup | Baseline RSS MiB | 1024 RSS MiB | Baseline .text B | 1024 .text B | GCC .text B | Baseline object B | 1024 object B | ELF SHA | Native C oracle |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        *summaries,
        "",
        "Each measurement is full CLI wall time (startup, check/lower, optimize,",
        "backend ELF emission, write), with peak process RSS from GNU time.",
        "No native runtime performance claim is derived from correctness checks.",
        "The sources, objects, executable oracles, hashes, and raw timing",
        "samples are retained as the CI artifact. This is a bounded",
        "synthetic but real checked-source experiment, not a policy change.",
    ]
    (out / "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report), flush=True)


if __name__ == "__main__":
    main()
