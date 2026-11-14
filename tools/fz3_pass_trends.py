#!/usr/bin/env python3
"""Persist exact source-pass activity from non-injected native FZ-3 campaigns.

These are observer-derived visits / textual IR edits, NOT proof of correctness.
A report from an injected self-test, malformed output, unvalidated campaign, or
unknown optimizer pass is rejected; no coverage certification is inferred.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fz3_triage as base

MODES = ("disabled", "scalar", "cost-aware")
CORE = (
    "pre_inline_promotion", "inlining", "post_inline_promotion", "sccp",
    "constant_fold", "array_fold", "algebra", "local_forward",
    "global_forward", "dse", "cse", "pre", "identical_merges",
    "if_conversion", "trivial_phi", "dead_phi", "cfg_bypass",
    "licm", "induction", "cfg_merge", "dce",
)
STAGES = tuple(f"{prefix}{pass_name}"
               for prefix in ("core.", "retry.") for pass_name in CORE) + (
                   "ctfe", "native_integer",
               )
CAMPAIGN_COLUMNS = ("mode", "stage", "visits", "edits")
RUN_COLUMNS = (
    "run_token", "source_sha", "mode", "stage",
    "visits", "edits", "truncated",
)
TOTAL_COLUMNS = (
    "mode", "stage", "runs", "visits", "edits",
    "runs_visited", "runs_edited",
)
REPORT_COLS = (
    "mode", "stage", "visits", "edits",
    "total_runs", "total_visits", "total_edits",
)
EXPECTED = {(mode, stage) for mode in MODES for stage in STAGES}


def parse_counts(report_dir: Path, sha: str) -> tuple[list[dict[str, str]], bool]:
    summary = base.read_summary(report_dir / "summary.txt")
    base.require(summary.get("source_sha") == sha,
                 "pass activity source SHA does not match current compiler")
    base.require(summary.get("injected") == "false",
                 "injected coverage MUST NEVER enter real pass history")
    base.require(summary.get("status") in ("no-mismatch", "native-wrong-value"),
                 "only completed real native campaigns can write pass history")
    base.require(summary.get("stopped_at_budget") in ("true", "false"),
                 "missing/invalid campaign budget status")
    base.require(base.UINT.fullmatch(summary.get("cases", "")) is not None,
                 "missing valid module recipe count")
    count = int(summary["cases"])
    base.require(count > 0, "empty native campaign must not count as pass activity")
    data = base.read_tsv(report_dir / "pass_coverage.tsv", CAMPAIGN_COLUMNS)
    base.require(len(data) == len(EXPECTED),
                 f"expected {len(EXPECTED)} mode/stage observations, got {len(data)}")
    found = set()
    for row in data:
        key = (row["mode"], row["stage"])
        base.require(key in EXPECTED, f"unrecognized TypeId optimizer stage {key}")
        base.require(key not in found, f"duplicate TypeId optimizer stage {key}")
        found.add(key)
        base.require(
            base.UINT.fullmatch(row["visits"]) is not None and
            base.UINT.fullmatch(row["edits"]) is not None,
            "pass counts must be nonnegative decimal integers")
        visits, edits = int(row["visits"]), int(row["edits"])
        base.require(edits <= visits <= count,
                     f"invalid pass activity counts for {key}")
    base.require(found == EXPECTED, "missing pass mode/stage row")
    # One successful source optimizer invocation observes each core pass once.
    # A truncated campaign may have no visits for later modes.
    for mode in MODES:
        count_by_core = {
            int(row["visits"]) for row in data
            if row["mode"] == mode and row["stage"].startswith("core.")
        }
        base.require(len(count_by_core) == 1,
                     f"inconsistent core stage visit totals in mode {mode}")
        expected_visits = next(iter(count_by_core))
        for stage in ("ctfe", "native_integer"):
            observed = next(int(row["visits"]) for row in data
                            if row["mode"] == mode and row["stage"] == stage)
            base.require(observed == expected_visits,
                         f"{mode} {stage} visits do not match core invocations")
    return data, summary["stopped_at_budget"] == "true"


def record(
    campaign: Path, history_dir: Path, output: Path,
    sha: str, token: str, url: str,
) -> dict[str, int]:
    base.require(base.HEX40.fullmatch(sha) is not None,
                 "pass trends require exact full compiler SHA")
    base.require(base.RUN_TOKEN.fullmatch(token) is not None, "invalid CI run token")
    base.require(url.startswith("https://github.com/bingordo/compiler-ci/actions/runs/"),
                 "unexpected native CI run URL")
    rows, truncated = parse_counts(campaign, sha)
    # Reuse the authoritative report and run verification. This prevents a
    # coverage file without a validated native campaign from entering history.
    _, digest = base.verify_campaign(campaign, sha)
    processed = base.read_tsv(
        history_dir / "processed_runs.tsv", base.PROCESSED_COLUMNS)
    match = [r for r in processed if r["run_token"] == token]
    base.require(len(match) == 1 and match[0]["source_sha"] == sha and
                 match[0]["run_url"] == url and
                 match[0]["report_digest"] == digest,
                 "native report must be validated by triage for this exact run first")
    path = history_dir / "pass_runs.tsv"
    past = base.read_tsv(path, RUN_COLUMNS)
    incoming = [{
        "run_token": token, "source_sha": sha, "mode": row["mode"],
        "stage": row["stage"], "visits": row["visits"],
        "edits": row["edits"], "truncated": str(truncated).lower(),
    } for row in rows]
    previous = [r for r in past if r["run_token"] == token]
    if previous:
        base.require(len(previous) == len(EXPECTED) and
                     sorted(previous, key=lambda r: (r["mode"], r["stage"])) ==
                     sorted(incoming, key=lambda r: (r["mode"], r["stage"])),
                     "same run reprocessed with changed native pass activity")
        replayed = 1
    else:
        base.require(len({(r["run_token"], r["mode"], r["stage"]) for r in past}) ==
                     len(past), "duplicate history mode/stage record")
        past.extend(incoming)
        replayed = 0

    past.sort(key=lambda r: (
        tuple(map(int, r["run_token"].split("."))), r["mode"], r["stage"],
    ))
    totals = {}
    for row in past:
        key = (row["mode"], row["stage"])
        result = totals.setdefault(key, {
            "mode": row["mode"], "stage": row["stage"],
            "runs": 0, "visits": 0, "edits": 0,
            "runs_visited": 0, "runs_edited": 0,
        })
        visits, edits = int(row["visits"]), int(row["edits"])
        result["runs"] += 1
        result["visits"] += visits
        result["edits"] += edits
        result["runs_visited"] += int(visits > 0)
        result["runs_edited"] += int(edits > 0)
    base.require(set(totals) == EXPECTED,
                 "history mode/stage list does not match current core registry")
    base.write_tsv(path, RUN_COLUMNS, past)
    base.write_tsv(history_dir / "pass_totals.tsv", TOTAL_COLUMNS, [
        {name: str(rec[name]) for name in TOTAL_COLUMNS}
        for key, rec in sorted(totals.items())
    ])
    output.mkdir(parents=True, exist_ok=True)
    by_key = {(r["mode"], r["stage"]): r for r in rows}
    base.write_tsv(output / "pass_trend.tsv", REPORT_COLS, [
        {
            "mode": key[0], "stage": key[1],
            "visits": by_key[key]["visits"], "edits": by_key[key]["edits"],
            "total_runs": str(totals[key]["runs"]),
            "total_visits": str(totals[key]["visits"]),
            "total_edits": str(totals[key]["edits"]),
        }
        for key in sorted(EXPECTED)
    ])
    edited = sorted(
        ((r["mode"], r["stage"], int(r["edits"])) for r in rows
         if int(r["edits"]) != 0),
        key=lambda x: (-x[2], x[0], x[1]),
    )
    rows_summary = [
        "# FZ-3 native TypeId stage activity trend",
        "",
        f"Compiler: \`{sha}\`; [CI run]({url})",
        "",
        f"- Raw output rows: **{len(rows)}** (3 modes × 44 stages)",
        f"- Stage/mode pairs that changed IR: **{len(edited)}**",
        f"- Bounded native campaign truncated: **{truncated}**",
        f"- Exact-run replay: **{bool(replayed)}**",
        "",
        "The stage observer measures **visits and changed serialized IR**.",
        "It does **not** certify optimizer-pass correctness.",
        "",
        "Most active edited stages:",
    ]
    for mode, stage, n in edited[:10]:
        rows_summary.append(f"- \`{mode}/{stage}\`: {n} changing invocation(s)")
    if not edited:
        rows_summary.append("- None in this bounded run")
    (output / "pass_trend.md").write_text(
        "\n".join(rows_summary) + "\n", encoding="utf-8")
    return {"rows": len(rows), "edited_stages": len(edited),
            "replayed": replayed, "truncated": int(truncated)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--campaign", type=Path, required=True)
    p.add_argument("--history-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--run-token", required=True)
    p.add_argument("--run-url", required=True)
    args = p.parse_args()
    try:
        print(record(args.campaign, args.history_dir, args.out,
                     args.source_sha, args.run_token, args.run_url))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"FZ-3 pass trend rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
