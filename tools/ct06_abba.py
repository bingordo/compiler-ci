#!/usr/bin/env python3
"""Same-runner ABBA comparison of two Mini-C compiler binaries.

Uses compiler's checked-source corpus/CLI profiling helpers. Each sample is
a *fresh compiler process*, emitting real ELF. Output hashes are compared
within each binary and across variants (cross-variant differences are reported,
not silently declared wrong code). No timing-based CI failure threshold.
"""
import argparse
import csv
import hashlib
import statistics
import sys
import tempfile
from pathlib import Path

FIELDS = (
    "case", "scale", "source_sha256", "variant", "order", "sample",
    "wall_ns", "user_s", "sys_s", "peak_rss_kb",
    "object_bytes", "text_bytes", "object_sha256",
)

def rounded_median(values):
    return statistics.median(values)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--helpers", required=True, type=Path,
                    help="Checkout containing tools/source_cli_profile.py")
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--before", required=True, type=Path)
    ap.add_argument("--after", required=True, type=Path)
    ap.add_argument("--samples", default=5, type=int,
                    help="ABBA rounds; each variant runs twice per round")
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    args = ap.parse_args()
    if not 3 <= args.samples <= 12:
        ap.error("samples must be 3..12 ABBA rounds")

    sys.path.insert(0, str(args.helpers.resolve()))
    from source_cli_profile import one_compile, read_manifest

    corpus = args.corpus.resolve()
    entries = read_manifest(corpus / "manifest.csv", corpus, "mini_c")
    binaries = {"before": args.before.resolve(), "after": args.after.resolve()}
    for name, binary in binaries.items():
        if not binary.is_file():
            raise FileNotFoundError(f"{name} compiler: {binary}")

    rows = []
    with tempfile.TemporaryDirectory(prefix="ct06-abba-") as tmp:
        directory = Path(tmp)
        out = directory / "compiled.o"
        timings = directory / "time.txt"
        for item in entries:
            source = corpus / item["path"]
            src_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            if src_hash != item["sha256"]:
                raise ValueError(f"source changed: {source}")
            expected = {}
            # Untimed warmup for both binary variants; one compiler process
            # each before collecting data for this particular source.
            for name, binary in binaries.items():
                expected[name] = one_compile(binary, source, out, timings)["object_sha256"]
            for round_index in range(args.samples):
                sequence = ("before", "after", "after", "before")
                if round_index % 2:
                    sequence = tuple(reversed(sequence))
                for position, name in enumerate(sequence):
                    metrics = one_compile(binaries[name], source, out, timings)
                    if metrics["object_sha256"] != expected[name]:
                        raise ValueError(
                            f"unstable ELF output: {item['case']}_{item['scale']} {name}")
                    rows.append({
                        "case": item["case"], "scale": int(item["scale"]),
                        "source_sha256": src_hash, "variant": name,
                        "order": round_index * 4 + position, "sample": round_index,
                        **metrics,
                    })
            print(f"ABBA {item['case']}/{item['scale']}: "
                  f"{2 * args.samples} samples each", flush=True)

    args.raw.parent.mkdir(parents=True, exist_ok=True)
    with args.raw.open("w", encoding="utf-8", newline="") as fd:
        writer = csv.DictWriter(fd, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    report = [
        "# CT-06 same-runner Mini-C compile comparison",
        "",
        f"ABBA rounds per case: {args.samples} (two observations per variant/round).",
        "Includes real frontend parsing, optimization and native ELF emission.",
        "All measurements from the same runner/job; no wall-time pass/fail gate.",
        "Different cross-variant ELF hashes are not alone a correctness failure;",
        "they require semantic/native validation.",
        "",
        "| Case | Scale | Before median ms | After median ms | Speedup | Before RSS MiB | After RSS MiB | ELF hashes |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in entries:
        case = item["case"]
        scale = int(item["scale"])
        select = lambda name: [
            row for row in rows
            if row["case"] == case and row["scale"] == scale
            and row["variant"] == name
        ]
        before = select("before")
        after = select("after")
        baseline = rounded_median([r["wall_ns"] for r in before]) / 1e6
        revised = rounded_median([r["wall_ns"] for r in after]) / 1e6
        before_rss = rounded_median([r["peak_rss_kb"] for r in before]) / 1024
        after_rss = rounded_median([r["peak_rss_kb"] for r in after]) / 1024
        same = before[0]["object_sha256"] == after[0]["object_sha256"]
        report.append(
            f"| {case} | {scale} | {baseline:.3f} | {revised:.3f} | "
            f"{baseline / revised:.2f}x | {before_rss:.1f} | "
            f"{after_rss:.1f} | {'same' if same else 'different'} |"
        )
    report.extend([
        "", "Full source SHA, toolchain/CPU information, raw subprocess CPU/RSS,",
        "source hash and emitted ELF sizes/hashes are retained alongside this report.",
        "This is a diagnostic sample, not proof of universal compile-time gains.",
    ])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report), flush=True)

if __name__ == "__main__":
    main()
