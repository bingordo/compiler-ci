#!/usr/bin/env python3
"""Compare the actual TypeId DCE pass with bounded direct or sparse SSA IDs.

Immutable-parent and candidate release binaries contain exactly the same
verified input generator. Collect 7-inner-repeat medians from both variants,
repeated with interleaved ABBA order; no whole-compiler speedup claims.
"""
import argparse
import csv
import re
import statistics
import subprocess
import tempfile
from pathlib import Path

RECORD = re.compile(r"BENCH,(1000|5000|10000),(live_chain|wide_dead),(1|1024),(\d+),(\d+),(\d+),(\d+)")
HEADERS = ["shape", "n", "stride", "round", "order", "variant",
           "removed", "median_pass_ns", "pass_allocs",
           "requested_bytes", "rss_kb"]

def measure(binary, rssfile):
    p = subprocess.run(["/usr/bin/time", "-f", "%M", "-o", str(rssfile),
                        str(binary)], capture_output=True, text=True,
                       timeout=120, check=True)
    cases = {}
    for line in p.stdout.splitlines():
        m = RECORD.fullmatch(line.strip())
        if not m:
            continue
        n, shape, stride, removed, ns, allocs, requested = m.groups()
        key = (shape, int(n), int(stride))
        if key in cases:
            raise RuntimeError(f"duplicate {key}")
        expected = int(n)-1 if shape == "wide_dead" else 0
        assert int(removed) == expected, (key, removed, expected)
        cases[key] = (expected, int(ns), int(allocs), int(requested))
    expected = {(shape,n,stride)
                for shape in ("live_chain","wide_dead")
                for n in (1000,5000,10000)
                for stride in (1,1024)}
    if cases.keys() != expected:
        raise RuntimeError(f"bad case keys: {cases.keys() ^ expected} stdout={p.stdout!r}")
    rss = int(rssfile.read_text().strip().splitlines()[-1])
    return cases,rss

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--before",type=Path,required=True)
    ap.add_argument("--after",type=Path,required=True)
    ap.add_argument("--rounds",type=int,default=3)
    ap.add_argument("--raw",type=Path,required=True)
    ap.add_argument("--report",type=Path,required=True)
    a=ap.parse_args()
    if not 2<=a.rounds<=10: ap.error("2 to 10 rounds")
    binaries={"hash":a.before.resolve(),"hybrid":a.after.resolve()}
    rows=[]
    with tempfile.TemporaryDirectory(prefix="ssa-ordinal-") as d:
        rssfile=Path(d)/"rss.txt"
        for binary in binaries.values(): measure(binary,rssfile)
        for round_no in range(a.rounds):
            order=("hash","hybrid","hybrid","hash")
            if round_no%2: order=tuple(reversed(order))
            for position,variant in enumerate(order):
                samples,rss=measure(binaries[variant],rssfile)
                for (shape,n,stride),(removed,ns,alloc,requested) in samples.items():
                    rows.append(dict(shape=shape,n=n,stride=stride,round=round_no,
                                     order=position,variant=variant,removed=removed,
                                     median_pass_ns=ns,pass_allocs=alloc,
                                     requested_bytes=requested,rss_kb=rss))
            print(f"ABBA round {round_no+1}/{a.rounds} complete",flush=True)
    a.raw.parent.mkdir(parents=True,exist_ok=True)
    with a.raw.open("w",newline="") as out:
        writer=csv.DictWriter(out,fieldnames=HEADERS)
        writer.writeheader();writer.writerows(rows)
    lines=[
        "# Bounded ordinal vs hash SSA definition index",
        "",
        "Actual TypeId DCE pass, including verifier, release mode. 7 inner medians",
        f"per binary, {a.rounds} interleaved ABBA rounds; raw RSS and timings attached.",
        "Graphs contain verified TypeId SSA. Dense stride=1, sparse stride=1024.",
        "Not whole-source compilation, PMU cache misses or a proof of prefetch activity.",
        "",
        "| Shape | Instructions | Stride | Hash ms | Hybrid ms | Speedup | Hash allocations | Hybrid allocations | Hash bytes | Hybrid bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for shape in ("live_chain","wide_dead"):
        for n in (1000,5000,10000):
            for stride in (1,1024):
                filtered=[r for r in rows if r["shape"]==shape and r["n"]==n and r["stride"]==stride]
                def median(var,key):
                    return statistics.median(r[key] for r in filtered if r["variant"]==var)
                ht=median("hash","median_pass_ns")
                ct=median("hybrid","median_pass_ns")
                ha=median("hash","pass_allocs")
                ca=median("hybrid","pass_allocs")
                hb=median("hash","requested_bytes")
                cb=median("hybrid","requested_bytes")
                lines.append(f"| {shape} | {n} | {stride} | {ht/1e6:.3f} | {ct/1e6:.3f} | {ht/ct:.2f}x | {ha:.0f} | {ca:.0f} | {hb:.0f} | {cb:.0f} |")
    a.report.write_text("\n".join(lines)+"\n")
    print("\n".join(lines),flush=True)

if __name__=="__main__":
    main()
