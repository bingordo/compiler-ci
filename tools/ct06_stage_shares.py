#!/usr/bin/env python3
"""Summarize existing opt-in checked Mini-C optimizer stage profile logs.

This is a *within-run*, *within-module-optimizer* stage breakdown, not a
whole-source performance profile. SOURCE_STAGE is the stage data from the
median optimization sample; every stage includes its own verification and
setup. No timing thresholds are used for correctness or CI.
"""
import argparse
import csv
from io import StringIO
from pathlib import Path


def read_profile(path):
    cases = {}
    stages = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "SOURCE_PROFILE,mini_c," in line:
            fields = next(csv.reader(StringIO(line.split("SOURCE_PROFILE,", 1)[1])))
            if len(fields) != 11 or fields[0] != "mini_c":
                raise ValueError(f"invalid Mini-C source profile row: {fields}")
            _, case, functions, raw_blocks, raw_insts, opt_blocks, opt_insts, selects, frontend, optimizer, elf = fields
            if case in cases:
                raise ValueError(f"duplicate source case {case}")
            cases[case] = {
                "functions": int(functions), "raw_blocks": int(raw_blocks),
                "raw_insts": int(raw_insts), "opt_blocks": int(opt_blocks),
                "opt_insts": int(opt_insts), "selects": int(selects),
                "frontend_ns": int(frontend), "optimizer_ns": int(optimizer),
                "elf_ns": int(elf),
            }
        elif "SOURCE_STAGE,mini_c," in line:
            fields = next(csv.reader(StringIO(line.split("SOURCE_STAGE,", 1)[1])))
            if len(fields) != 5 or fields[0] != "mini_c":
                raise ValueError(f"invalid stage row: {fields}")
            _, case, stage, ns, calls = fields
            if (case, stage) in stages:
                raise ValueError(f"duplicate stage: {case}/{stage}")
            stages[case, stage] = (int(ns), int(calls))
    if not cases or len(cases) < 9:
        raise ValueError(f"expected at least 9 checked Mini-C cases; got {len(cases)}")
    for case, details in cases.items():
        if details["optimizer_ns"] <= 0 or details["raw_blocks"] < 1:
            raise ValueError(f"invalid source optimizer observation: {case}")
        for stage in ("mem2reg", "sccp", "ifconv_scalar", "module_verify"):
            if (case, stage) not in stages:
                raise ValueError(f"missing stage {case}/{stage}")
            ns, calls = stages[case, stage]
            if ns <= 0 or calls < 1:
                raise ValueError(f"invalid stage {case}/{stage}: {ns}/{calls}")
    return cases, stages


def build_report(cases, stages):
    out = [
        "# CT-06 Mini-C optimizer stage shares",
        "",
        "Each program was parsed and lowered by the real checked Mini-C frontend.",
        "These are the **median optimizer sample's** pass timings, including each",
        "pass's own verifier/setup, not the whole source-to-ELF wall time.",
        "The whole optimizer includes all scalar pass re-runs, CTFE and inlining.",
        "Shares use the measured module optimizer wall time as denominator;",
        "do not add percentages of overlapping/other-stage clock domains.",
        "Time variation between runners and diagnostic overhead are not normalized.",
        "",
        "| Case | Raw blocks / IR insts | Optimized blocks | Optimizer ms | mem2reg ms (% of optimizer) | SCCP ms (% of optimizer) | Top optimizer stages |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    rows = []
    for case in sorted(cases):
        summary = cases[case]
        ns = summary["optimizer_ns"]
        mns, mcalls = stages[case, "mem2reg"]
        sns, scalls = stages[case, "sccp"]
        ranked = sorted(
            ((stage, duration) for (c, stage), (duration, _) in stages.items() if c == case),
            key=lambda p: p[1], reverse=True
        )
        top = ", ".join(f"{stage} {elapsed/1e6:.3f}ms" for stage, elapsed in ranked[:4])
        out.append(
            f"| {case} | {summary['raw_blocks']} / {summary['raw_insts']} | "
            f"{summary['opt_blocks']} | {ns/1e6:.3f} | "
            f"{mns/1e6:.3f} ({100*mns/ns:.1f}%) | "
            f"{sns/1e6:.3f} ({100*sns/ns:.1f}%) | {top} |"
        )
        rows.append({
            "case": case,
            **summary,
            "mem2reg_ns": mns, "mem2reg_calls": mcalls,
            "sccp_ns": sns, "sccp_calls": scalls,
        })
    out.extend([
        "",
        "Stage times are from an *opt-in profiled compilation*, not from",
        "the ordinary non-instrumented compiler. Treat single-digit microseconds",
        "and differences between cases as descriptive, not acceptance thresholds.",
        "Use the separate source-to-ELF ABBA evidence for end-to-end latency/RSS.",
        "",
    ])
    for prefix in ("ct05_branches",):
        sub = [(int(case.split("_")[-1]), cases[case], stages[case, "sccp"][0],
                stages[case, "mem2reg"][0])
               for case in cases if case.startswith(prefix+"_")]
        sub.sort()
        if len(sub) >= 2:
            out += ["## Branch-chain scaling", "",
                    "| Scale | Raw blocks | SCCP µs | mem2reg µs |",
                    "|---:|---:|---:|---:|"]
            for size, data, sccp, mem2reg in sub:
                out.append(f"| {size} | {data['raw_blocks']} | {sccp/1000:.1f} | {mem2reg/1000:.1f} |")
            out.append("")
    return "\n".join(out), rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    cases, stages = read_profile(args.log)
    report, rows = build_report(cases, stages)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    with args.csv.open("w", newline="", encoding="utf-8") as fd:
        writer = csv.DictWriter(fd, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(report)


if __name__ == "__main__":
    main()
