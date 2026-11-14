#!/usr/bin/env python3
"""Archive exact native trap witnesses and conservatively recheck old ones.

A saved v2 witness must reproduce its pinned TypeId reference/native outcome
pair before history publication. The pinned CLI cannot distinguish agreement
from a changed wrong outcome on exit 1, so those results are NOT called fixes.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import fz3_triage as base
import fz3_outcome_triage as v2

STATUS_COLUMNS = ("id", "state", "last_sha", "last_run_token", "last_run", "detail")
STATES = {"reproduces", "not-exact", "unavailable"}
RESULT = re.compile(
    r"OUTCOME_MISMATCH a=(-?[0-9]+) b=(-?[0-9]+) "
    r"expected=(sigfpe|value:[0-9]+) observed=(sigfpe|value:[0-9]+)\Z"
)
SAFE_ID = re.compile(r"[a-f0-9]{20}\Z")


def witness_path(root: Path, ident: str) -> Path:
    base.require(SAFE_ID.fullmatch(ident) is not None, "unsafe v2 witness ID")
    return root / "outcome_witnesses_v2" / f"{ident}.ir"


def classify(oracle: Path, witness: Path, row: dict[str, str],
             timeout: int) -> tuple[str, str]:
    args = [
        str(oracle.resolve()), str(witness.resolve()),
        "--a", row["a"], "--b", row["b"], "--sink", row["sink"],
        "--expected-outcome", row["expected"],
        "--observed-outcome", row["actual"],
    ]
    if row["mode"] != "raw":
        args.extend(["--optimize", row["mode"]])
    try:
        result = subprocess.run(args, check=False, capture_output=True,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "unavailable", f"execution unavailable: {error}"[:180]
    if result.returncode == 1:
        return "not-exact", "pinned outcome absent; no fix is inferred"
    if result.returncode != 0:
        return "unavailable", (result.stderr.strip() or
                               f"oracle exited {result.returncode}")[:180]
    match = RESULT.fullmatch(result.stdout.strip())
    if match is None or match.groups() != (
        row["a"], row["b"], row["expected"], row["actual"]
    ):
        return "unavailable", "malformed or mismatched pinned oracle result"
    return "reproduces", "exact original-reference/native outcome reproduced"


def recheck(report: Path, history_dir: Path, triage_dir: Path,
            out: Path, oracle: Path, sha: str, token: str, url: str,
            limit: int = 16, timeout: int = 40) -> dict[str, int]:
    base.require(1 <= limit <= 128 and 1 <= timeout <= 180,
                 "outcome recheck budget out of bounds")
    base.require(oracle.is_file(), "native outcome oracle executable missing")
    rows, digest = v2.verify_campaign(report, sha)
    run = base.read_tsv(history_dir / "outcome_processed_runs_v2.tsv",
                        v2.PROCESSED_COLUMNS)
    exact = [r for r in run if r["run_token"] == token]
    base.require(len(exact) == 1 and exact[0]["source_sha"] == sha and
                 exact[0]["run_url"] == url and exact[0]["report_digest"] == digest,
                 "v2 reconciliation for exact run must succeed before recheck")
    history = base.read_tsv(history_dir / "outcome_fingerprints_v2.tsv",
                            v2.HISTORY_COLUMNS)
    v2.validate_previous(history, run)
    current = {v2.key(r): r for r in rows}
    newly_reported = base.read_tsv(triage_dir / "outcome_new_v2.tsv",
                                   v2.NEW_COLUMNS)
    if not (triage_dir / "outcome_new_v2.tsv").is_file():
        raise ValueError("missing v2 new-fingerprint roster")
    history_by_id = {row["id"]: row for row in history}
    prior_status = base.read_tsv(history_dir / "outcome_rechecks_v2.tsv", STATUS_COLUMNS)
    state = {}
    for row in prior_status:
        base.require(row["id"] not in state and row["id"] in history_by_id and
                     row["state"] in STATES and
                     base.RUN_TOKEN.fullmatch(row["last_run_token"]) is not None,
                     "corrupt or orphan v2 witness lifecycle")
        state[row["id"]] = row

    # First validate every new item against the live exact-source compiler.
    # A malformed, missing, or non-reproducing witness aborts the entire run
    # BEFORE copying any new files into the durable branch workspace.
    copies: list[tuple[Path, Path, dict[str, str]]] = []
    new_ids: set[str] = set()
    for row in newly_reported:
        ident = row["id"]
        base.require(ident not in new_ids and ident in history_by_id and
                     v2.fingerprint_id(row) == ident and
                     v2.key(row) in current and
                     ident not in state, "invalid or repeated new v2 finding")
        new_ids.add(ident)
        source = v2.safe_witness(report, current[v2.key(row)]["minimized_ir"],
                                 "minimized.ir")
        target = witness_path(history_dir, ident)
        status, detail = classify(oracle, source, row, timeout)
        base.require(status == "reproduces",
                     f"new minimized v2 witness did not reproduce: {detail}")
        if target.exists():
            base.require(target.read_bytes() == source.read_bytes(),
                         "distinct witnesses collide at same v2 fingerprint ID")
        copies.append((source, target, row))
    # The triage's new list must be exactly all keys first introduced in this
    # run, not a forged partial list that hides a required archive.
    base.require(all(row["id"] in history_by_id for row in newly_reported),
                 "new v2 issue roster references unknown fingerprint")
    for source, target, row in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        state[row["id"]] = {
            "id": row["id"], "state": "reproduces",
            "last_sha": sha, "last_run_token": token, "last_run": url,
            "detail": "first exact-source replay succeeded",
        }

    older = [r for r in history if r["id"] not in new_ids]
    if older:
        # Rotate a bounded window to avoid permanently starving old cases.
        start = (int(token.split(".")[0]) * limit) % len(older)
        selected = (older[start:] + older[:start])[:limit]
    else:
        selected = []
    counts = {"reproduces": 0, "not-exact": 0, "unavailable": 0}
    for row in selected:
        ident = row["id"]
        previous = state.get(ident)
        if previous and previous["last_run_token"] == token:
            continue
        witness = witness_path(history_dir, ident)
        if witness.is_file():
            status, detail = classify(oracle, witness, row, timeout)
        else:
            status, detail = "unavailable", "archived minimized witness missing"
        counts[status] += 1
        state[ident] = {
            "id": ident, "state": status, "last_sha": sha,
            "last_run_token": token, "last_run": url,
            "detail": detail.replace("\t", " ").replace("\n", " ")[:180],
        }
    out.mkdir(parents=True, exist_ok=True)
    base.write_tsv(history_dir / "outcome_rechecks_v2.tsv", STATUS_COLUMNS,
                   sorted(state.values(), key=lambda row: row["id"]))
    (out / "outcome_recheck_v2.md").write_text(
        "# FZ-3 v2 archived trap/return rechecks\n\n"
        f"Verified and archived new exact outcomes: {len(copies)}\n\n"
        f"Previously archived checked: {sum(counts.values())}\n\n"
        f"Exact reproductions: {counts['reproduces']}; "
        f"not exact: {counts['not-exact']}; "
        f"unavailable: {counts['unavailable']}.\n\n"
        "An absent pinned signature is NOT proof of agreement, nor of a fix. "
        "There is no automatic issue closing or regression attribution.\n",
        encoding="utf-8",
    )
    return {"archived": len(copies), **counts}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("campaign", "history-dir", "triage-dir", "out", "oracle"):
        p.add_argument("--" + flag, type=Path, required=True)
    for flag in ("source-sha", "run-token", "run-url"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--limit", type=int, default=16)
    p.add_argument("--timeout", type=int, default=40)
    a = p.parse_args()
    try:
        print(recheck(a.campaign, a.history_dir, a.triage_dir, a.out,
                      a.oracle, a.source_sha, a.run_token, a.run_url,
                      a.limit, a.timeout))
    except (ValueError, OSError, KeyError) as error:
        print(f"FZ-3 v2 outcome replay rejected: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
