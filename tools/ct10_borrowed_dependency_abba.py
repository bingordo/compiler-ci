#!/usr/bin/env python3
"""Same-input, same-runner ABBA for complete TypeId DCE dependency indexing.

Each binary is built from a pinned compiler commit with the *same* synthetic
verified IR fixture. The binary's seven-sample medians report pass-only time,
allocator calls, and allocator-request bytes. RSS is full process peak.
No timing-based acceptance gate; native correctness has separate test gates.
"""
import argparse
import csv
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

RECORD = re.compile(r"^BENCH,(\d+),(live_chain|wide_dead),(\d+),(\d+),(\d+),(\d+)$")
FIELDS = ("case", "n", "round", "order", "variant", "removed",
          "median_pass_ns", "pass_allocs", "pass_requested_bytes", "process_rss_kb")


def measure(binary: Path, rss_file: Path):
    proc = subprocess.run(
        ["/usr/bin/time", "-f", "%M", "-o", str(rss_file), str(binary)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    parsed = {}
    for line in proc.stdout.splitlines():
        match = RECORD.fullmatch(line)
        if match is None:
            continue
        n, case, removed, duration, allocs, requested = match.groups()
        key = (case, int(n))
        if key in parsed:
            raise AssertionError(f"duplicate probe record {key}")
        expected = int(n) - 1 if case == "wide_dead" else 0
        if int(removed) != expected:
            raise AssertionError(f"{key}: unexpected DCE removals {removed}")
        parsed[key] = {
            "removed": int(removed),
            "median_pass_ns": int(duration),
            "pass_allocs": int(allocs),
            "pass_requested_bytes": int(requested),
        }
    expected_keys = {(case, n) for case in ("live_chain", "wide_dead")
                     for n in (1000, 5000, 10000)}
    if set(parsed) != expected_keys:
        raise AssertionError(f"missing probe cases {expected_keys ^ set(parsed)};"
                             f" stdout={proc.stdout!r} stderr={proc.stderr!r}")
    rss = int(rss_file.read_text().strip().splitlines()[-1])
    return parsed, rss


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    if not 2 <= a.rounds <= 10:
        p.error("--rounds must be 2 through 10")
    binaries = {"before": a.before.resolve(), "after": a.after.resolve()}
    for binary in binaries.values():
        if not binary.is_file():
            raise FileNotFoundError(binary)
    rows = []
    with tempfile.TemporaryDirectory(prefix="ct10-deps-") as d:
        rss_file = Path(d) / "peak.txt"
        for binary in binaries.values():
            measure(binary, rss_file)
        for round_no in range(a.rounds):
            order = ("before", "after", "after", "before")
            if round_no % 2:
                order = tuple(reversed(order))
            for position, variant in enumerate(order):
                data, rss = measure(binaries[variant], rss_file)
                for (case, n), result in data.items():
                    rows.append({
                        "case": case, "n": n, "round": round_no, "order": position,
                        "variant": variant, **result, "process_rss_kb": rss,
                    })
            print(f"ABBA completed round {round_no + 1}/{a.rounds}", flush=True)

    a.raw.parent.mkdir(parents=True, exist_ok=True)
    with a.raw.open("w", encoding="utf-8", newline="") as output:
        w = csv.DictWriter(output, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    summary = [
        "# Complete TypeId DCE borrowed-dependency ABBA",
        "",
        "Exact-parent vs candidate release builds with identical verified TypeId",
        "SSA test inputs; time/allocations apply to actual DCE (including its",
        "entry verifier) but exclude SSA construction, CLI process startup",
        "and initial module clones. Seven inner medians in each sample and",
        f"{a.rounds} ABBA rounds, two processes per variant per round.",
        "This does not measure hardware cache misses or full source compilation.",
        "",
        "| Graph | Instr | Parent DCE ms | Borrowed DCE ms | Speedup | Parent alloc | Borrowed alloc | Parent bytes | Borrowed bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in ("live_chain", "wide_dead"):
        for n in (1000, 5000, 10000):
            before = [r for r in rows if r["case"] == case and r["n"] == n
                      and r["variant"] == "before"]
            after = [r for r in rows if r["case"] == case and r["n"] == n
                     and r["variant"] == "after"]
            med = lambda vals, key: statistics.median(row[key] for row in vals)
            bt = med(before, "median_pass_ns")
            at = med(after, "median_pass_ns")
            ba = med(before, "pass_allocs")
            aa = med(after, "pass_allocs")
            bb = med(before, "pass_requested_bytes")
            ab = med(after, "pass_requested_bytes")
            summary.append(
                f"| {case} | {n} | {bt / 1e6:.3f} | {at / 1e6:.3f} | "
                f"{bt / at:.2f}× | {ba:.0f} | {aa:.0f} | {bb:.0f} | {ab:.0f} |"
            )
    summary.extend(["", "Raw per-process data and RSS in attached CSV.",
                    "No performance threshold gates CI; correctness must pass independently."])
    a.report.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary), flush=True)


if __name__ == "__main__":
    main()
