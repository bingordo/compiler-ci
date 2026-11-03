#!/usr/bin/env python3
"""CT-05 paired ABBA pass benchmark, no timing-based CI gate."""
import argparse
import csv
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

CASES = [(16, 10), (64, 4), (128, 2), (256, 1)]
REGEX = re.compile(r"CT05 size=(\d+) iterations=(\d+) ns=(\d+) signature=([a-f0-9]{16})")
FIELDS = ("size", "iterations", "round", "order", "variant", "elapsed_ns",
          "ns_per_iteration", "rss_kb", "signature")


def run(binary, n, iterations, rss):
    process = subprocess.run(
        ["/usr/bin/time", "-f", "%M", "-o", str(rss),
         str(binary), str(n), str(iterations)],
        check=True, capture_output=True, text=True, timeout=90,
    )
    match = REGEX.fullmatch(process.stdout.strip())
    if match is None:
        raise ValueError(f"{binary}: invalid benchmark output: {process.stdout!r}")
    actual_n, actual_iter, elapsed, signature = match.groups()
    assert (int(actual_n), int(actual_iter)) == (n, iterations)
    return {
        "elapsed_ns": int(elapsed),
        "ns_per_iteration": int(elapsed) / iterations,
        "rss_kb": int(rss.read_text().strip().splitlines()[-1]),
        "signature": signature,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.rounds <= 12:
        parser.error("rounds out of bounds")
    binaries = {"before": args.before.resolve(), "after": args.after.resolve()}
    for name, binary in binaries.items():
        if not binary.is_file():
            raise FileNotFoundError(f"{name}: {binary}")

    rows = []
    with tempfile.TemporaryDirectory(prefix="ct05-identical-") as scratch:
        rss = Path(scratch) / "rss.txt"
        for n, iterations in CASES:
            expected = {name: run(binary, n, iterations, rss)["signature"]
                        for name, binary in binaries.items()}
            if expected["before"] != expected["after"]:
                raise AssertionError(f"{n}: before and after SSA differ: {expected}")
            for round_index in range(args.rounds):
                sequence = ["before", "after", "after", "before"]
                if round_index % 2:
                    sequence.reverse()
                for order, name in enumerate(sequence):
                    observed = run(binaries[name], n, iterations, rss)
                    if observed["signature"] != expected[name]:
                        raise AssertionError(f"{n}: nondeterministic SSA {name}")
                    rows.append({
                        "size": n, "iterations": iterations,
                        "round": round_index, "order": order, "variant": name,
                        **observed,
                    })
            print(f"size={n}: {2 * args.rounds} independent processes per variant, "
                  "identical transformed SSA", flush=True)

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report = [
        "# CT-05 identical-successor branch batching, same-runner ABBA",
        "",
        f"{args.rounds} alternating ABBA rounds; two fresh processes per variant per round.",
        "Each timing includes cloning verified typed SSA and executing the actual",
        "optimizer if-conversion pass, but excludes process startup.",
        "One equal-successor chain contains exactly one intentionally unequal",
        "parallel-edge branch, which is retained in both outputs.",
        "SSA output signatures must match; latency does not gate CI.",
        "",
        "| Chain blocks | Before median ms | After median ms | Speedup | Before peak RSS MiB | After peak RSS MiB | SSA |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for n, _ in CASES:
        def med(name, key):
            return statistics.median(
                r[key] for r in rows if r["size"] == n and r["variant"] == name)
        before = med("before", "ns_per_iteration") / 1e6
        after = med("after", "ns_per_iteration") / 1e6
        before_rss = med("before", "rss_kb") / 1024
        after_rss = med("after", "rss_kb") / 1024
        report.append(
            f"| {n} | {before:.3f} | {after:.3f} | {before / after:.2f}x "
            f"| {before_rss:.1f} | {after_rss:.1f} | same |"
        )
    report.extend([
        "",
        "Raw process samples, exact compiler revisions, CPU and Rust toolchain",
        "and fixture hash are in the artifact. Constructed verified SSA only:",
        "**not** a source-to-ELF benchmark or a universal speedup.",
    ])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report), flush=True)


if __name__ == "__main__":
    main()
