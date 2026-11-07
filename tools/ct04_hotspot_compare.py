#!/usr/bin/env python3
"""ABBA comparison of exact compiler revisions on verifier-valid SSA stress cases.

Both binaries use the same injected Rust fixture source. Every sample is a
fresh process, but measured nanoseconds come from the in-process optimizer/
verifier loop, excluding process startup. Peak RSS comes from GNU time.
No timing threshold fails CI; semantic/result signature parity does.
"""
import argparse
import csv
import json
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

CASES = {
    "ct03c": [
        ("mem2reg_slots", 32, 12),
        ("mem2reg_slots", 96, 6),
        ("mem2reg_slots", 192, 3),
        ("mem2reg_slots", 256, 2),
    ],
    "ct03d": [
        ("sccp_reverse", 32, 12),
        ("sccp_reverse", 96, 6),
        ("sccp_reverse", 256, 2),
        ("sccp_forward", 32, 12),
        ("sccp_forward", 96, 6),
        ("sccp_forward", 256, 2),
    ],
    "ct03induction": [
        ("induction_products", 1, 12),
        ("induction_products", 4, 8),
        ("induction_products", 8, 4),
        ("induction_products", 16, 2),
    ],
    "ct03licm": [
        ("licm_preheader", 24, 12),
        ("licm_preheader", 96, 6),
        ("licm_preheader", 256, 3),
        ("licm_hoist", 24, 12),
        ("licm_hoist", 96, 6),
        ("licm_hoist", 256, 3),
        ("licm_pipeline", 24, 12),
        ("licm_pipeline", 96, 6),
        ("licm_pipeline", 256, 3),
    ],
    "ct03scalar": [
        ("sccp_forward", 32, 12),
        ("sccp_forward", 96, 6),
        ("sccp_forward", 256, 2),
        ("sccp_reverse", 32, 12),
        ("sccp_reverse", 96, 6),
        ("sccp_reverse", 256, 2),
    ],
    "ct03typed": [
        ("typed_sccp_fold", 32, 12),
        ("typed_sccp_fold", 96, 6),
        ("typed_sccp_fold", 256, 2),
        ("typed_sccp_noop", 32, 12),
        ("typed_sccp_noop", 96, 6),
        ("typed_sccp_noop", 256, 2),
        ("typed_sccp_many", 32, 3),
        ("typed_sccp_many", 96, 2),
    ],
    "ct04a": [
        ("pointer_reverse", 64, 8),
        ("pointer_reverse", 128, 4),
        ("pointer_reverse", 256, 2),
        ("pointer_unknown", 128, 4),
        ("pointer_wide", 128, 8),
        ("pointer_wide", 256, 4),
    ],
    "ct04b": [
        ("phi_reverse", 64, 8),
        ("phi_reverse", 128, 4),
        ("phi_reverse", 256, 2),
        ("phi_wide", 128, 8),
        ("phi_wide", 256, 4),
        ("phi_wide", 512, 2),
    ],
    # CT-04c compares the independent-phi fast path with its exact parent
    # while retaining all dependent reverse-chain regression controls.
    "ct04c": [
        ("phi_reverse", 64, 8),
        ("phi_reverse", 128, 4),
        ("phi_reverse", 256, 2),
        ("phi_wide", 128, 8),
        ("phi_wide", 256, 4),
        ("phi_wide", 512, 2),
    ],
}
FIELDS = (
    "pair", "case", "size", "round", "order", "variant", "iterations",
    "work_ns", "ns_per_iter", "peak_rss_kb", "signature",
)
RESULT = re.compile(
    r"^CT0[46] case=(\w+) size=(\d+) iterations=(\d+) "
    r"ns=(\d+) signature=([0-9a-f]{16})$"
)


def measure(binary, case, size, iterations, timing):
    result = subprocess.run(
        ["/usr/bin/time", "-f", "%M", "-o", str(timing),
         str(binary), case, str(size), str(iterations)],
        text=True, capture_output=True, timeout=150, check=True,
    )
    matched = RESULT.fullmatch(result.stdout.strip())
    if matched is None:
        raise ValueError(
            f"{binary}: unexpected result {result.stdout!r} / {result.stderr!r}"
        )
    observed_case, observed_size, observed_iterations, work_ns, signature = matched.groups()
    if (observed_case, int(observed_size), int(observed_iterations)) != (
        case, size, iterations
    ):
        raise ValueError("benchmark returned a different fixture")
    return {
        "work_ns": int(work_ns),
        "ns_per_iter": int(work_ns) / iterations,
        "peak_rss_kb": int(timing.read_text().strip().splitlines()[-1]),
        "signature": signature,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", choices=tuple(CASES), required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.rounds <= 12:
        parser.error("rounds must be between 3 and 12")
    binaries = {"before": args.before.resolve(), "after": args.after.resolve()}
    if not all(binary.is_file() for binary in binaries.values()):
        raise FileNotFoundError("one or both release benchmark binaries are missing")

    rows = []
    with tempfile.TemporaryDirectory(prefix="ct04-same-runner-") as directory:
        timing = Path(directory) / "rss.txt"
        for case, size, iterations in CASES[args.pair]:
            signatures = {}
            # Untimed warmup per binary, allowing module loading/initial setup.
            for variant, binary in binaries.items():
                signatures[variant] = measure(
                    binary, case, size, iterations, timing)["signature"]
            if signatures["before"] != signatures["after"]:
                raise AssertionError(
                    f"before/after IR/result differ for {case}/{size}: {signatures}"
                )
            for round_index in range(args.rounds):
                # Reverse successive ABBA rounds to control drift/order.
                sequence = ("before", "after", "after", "before")
                if round_index % 2:
                    sequence = tuple(reversed(sequence))
                for order, variant in enumerate(sequence):
                    metrics = measure(
                        binaries[variant], case, size, iterations, timing
                    )
                    if metrics["signature"] != signatures[variant]:
                        raise AssertionError(
                            f"nondeterministic result for {case}/{size}/{variant}"
                        )
                    rows.append({
                        "pair": args.pair, "case": case, "size": size,
                        "round": round_index, "order": order, "variant": variant,
                        "iterations": iterations, **metrics
                    })
            print(f"{case}/{size}: {2 * args.rounds} processes per variant; "
                  f"identical output signatures", flush=True)

    args.raw.parent.mkdir(parents=True, exist_ok=True)
    with args.raw.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    report = [
        f"# {'CT-04' if args.pair.startswith('ct04') else 'CT-06'} {args.pair} isolated optimizer benchmark",
        "",
        "Same-runner release-built binaries, identical injected Rust SSA fixtures.",
        f"{args.rounds} interleaved ABBA rounds, two processes per variant per round.",
        "Measured ns/iteration includes fixture cloning, the *actual*",
        "IR optimizer/verifier pass, and result destruction (not process startup).",
        "Peak RSS is per-process GNU time; output signatures match.",
        "These artificial verified SSA stress cases do **not** measure",
        "end-to-end C or Oberon source-to-ELF compile latency.",
        "No timing-based CI gate or claimed universal speedup.",
        "",
        "| Case | Size | Before ms/iteration | After ms/iteration | Speedup | Before RSS MiB | After RSS MiB | Result |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case, size, iterations in CASES[args.pair]:
        sample = lambda name: [
            row for row in rows
            if row["case"] == case and row["size"] == size
            and row["variant"] == name
        ]
        before, after = sample("before"), sample("after")
        baseline = statistics.median(row["ns_per_iter"] for row in before) / 1e6
        updated = statistics.median(row["ns_per_iter"] for row in after) / 1e6
        before_rss = statistics.median(row["peak_rss_kb"] for row in before) / 1024
        after_rss = statistics.median(row["peak_rss_kb"] for row in after) / 1024
        report.append(
            f"| {case} | {size} | {baseline:.3f} | {updated:.3f} | "
            f"{baseline / updated:.2f}x | {before_rss:.1f} | {after_rss:.1f} | same |"
        )
    report.extend([
        "", "All per-process timings, individual source/result signatures and",
        "peak RSS observations are in the attached CSV. Pair-specific compiler",
        "source SHAs, rustc version and CPU model are separate provenance files.",
    ])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report), flush=True)


if __name__ == "__main__":
    main()
