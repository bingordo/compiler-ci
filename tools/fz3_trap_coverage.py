#!/usr/bin/env python3
"""Verify that the complete native FZ-3 campaign actually exercised traps.

This read-only gate validates independent original-TypeId reference outcomes
observed during real GAS and direct-ELF executions. It cannot manufacture
fingerprints or modify v1/v2 durable history.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fz3_triage as base

COLUMNS = ("profile", "mode", "sink", "defined", "zero_sigfpe", "overflow_sigfpe")
PROFILES = ("signed-div-alias", "signed-rem-alias")
MODES = ("raw", "disabled", "scalar", "cost-aware")
SINKS = ("gas", "elf")
EXPECTED = {(p, m, sink) for p in PROFILES for m in MODES for sink in SINKS}


def verify(report: Path, source_sha: str) -> dict[str, int]:
    """Require complete defined/zero-divisor/overflow coverage in both recipes."""
    base.require(base.HEX40.fullmatch(source_sha) is not None,
                 "invalid exact compiler SHA")
    summary = base.read_summary(report / "summary.txt")
    base.require(summary.get("injected") == "false",
                 "injected campaigns never satisfy production trap coverage")
    base.require(summary.get("source_sha") == source_sha,
                 "trap coverage source SHA mismatch")
    base.require(summary.get("status") in ("no-mismatch", "native-wrong-value"),
                 "invalid/incomplete native campaign status")
    expected_counts = {
        "seeds_per_profile": "2", "max_evaluations": "240", "evaluations": "240",
        "cases": "10", "stopped_at_budget": "false",
    }
    for key, expected in expected_counts.items():
        base.require(summary.get(key) == expected,
                     f"invalid or incomplete nightly trap coverage: {key}")
    base.require(base.UINT.fullmatch(summary.get("first_seed", "")) is not None and
                 int(summary["first_seed"]) % 2 == 0,
                 "two campaign seeds must begin with even seed (DivS then RemS)")
    path = report / "trap_coverage.tsv"
    base.require(path.is_file(), "missing native trap coverage report")
    rows = base.read_tsv(path, COLUMNS)
    base.require(len(rows) == len(EXPECTED),
                 "wrong number of signed trap profile/mode/sink rows")
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row["profile"], row["mode"], row["sink"])
        base.require(key in EXPECTED and key not in seen,
                     f"unknown or duplicate signed trap coverage row: {key}")
        seen.add(key)
        for kind in ("defined", "zero_sigfpe", "overflow_sigfpe"):
            base.require(row[kind] == "1",
                         f"missing/noncanonical {kind} execution in {key}")
    base.require(seen == EXPECTED, "missing a signed trap profile or native sink")
    return {
        "verified_profile_modes_sinks": len(rows),
        "defined_executions": len(rows),
        "zero_sigfpe_executions": len(rows),
        "overflow_sigfpe_executions": len(rows),
    }


def main() -> int:
    """Validate a source-pinned report and print its coverage gate result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        print(verify(args.campaign, args.source_sha))
    except (OSError, ValueError, KeyError) as error:
        print(f"FZ-3 trap coverage rejected: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
