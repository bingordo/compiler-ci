#!/usr/bin/env python3
"""Archive exact native trap witnesses and track independently verified outcomes.

The unpinned TypeId oracle reports original-reference and compiled-native
outcomes even when they agree. Only distinct source revisions with identical
original-reference semantics establish tentative agreement evidence.
Infrastructure failures and reference drift never imply a fix.
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
STATES = {"reproduces", "not-exact", "agrees", "changed-mismatch", "unavailable"}
AGREEMENT_COLUMNS = ("id", "first_agree_sha", "second_agree_sha")
EVENT_COLUMNS = ("id", "event", "source_sha", "run_url", "detail")
EVENTS = {"suspected-fix", "second-revision-agreement", "regression", "changed-outcome"}
RESULT = re.compile(
    r"OUTCOMES_V1 a=(-?[0-9]+) b=(-?[0-9]+) "
    r"expected=(sigfpe|value:[0-9]+) observed=(sigfpe|value:[0-9]+)\Z"
)
SAFE_ID = re.compile(r"[a-f0-9]{20}\Z")


def witness_path(root: Path, ident: str) -> Path:
    base.require(SAFE_ID.fullmatch(ident) is not None, "unsafe v2 witness ID")
    return root / "outcome_witnesses_v2" / f"{ident}.ir"


def classify(oracle: Path, witness: Path, row: dict[str, str],
             timeout: int) -> tuple[str, str]:
    """Classify actual typed outcomes; never infer a fix from an absent pin."""
    args = [
        str(oracle.resolve()), str(witness.resolve()),
        "--a", row["a"], "--b", row["b"], "--sink", row["sink"],
        "--report-outcomes",
    ]
    if row["mode"] != "raw":
        args.extend(["--optimize", row["mode"]])
    try:
        result = subprocess.run(args, check=False, capture_output=True,
                                text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "unavailable", f"execution unavailable: {error}"[:180]
    if result.returncode != 0:
        return "unavailable", (result.stderr.strip() or
                               f"oracle exited {result.returncode}")[:180]
    match = RESULT.fullmatch(result.stdout.strip())
    if match is None:
        return "unavailable", "malformed or non-versioned native outcome report"
    a, b, expected, actual = match.groups()
    if (a, b) != (row["a"], row["b"]) or not (
        v2.valid_outcome(expected) and v2.valid_outcome(actual)
    ):
        return "unavailable", "noncanonical outcome or triggering-input mismatch"
    if expected != row["expected"]:
        return "unavailable", (
            f"independent original-reference drift: {row['expected']} -> {expected}"
        )
    if actual == row["actual"]:
        return "reproduces", "exact native trap/return signature still reproduces"
    if actual == expected:
        return "agrees", "native execution agrees with the independent original reference"
    return "changed-mismatch", f"native outcome changed to {actual}; still incorrect"


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
    agreement_rows = base.read_tsv(
        history_dir / "outcome_agreements_v2.tsv", AGREEMENT_COLUMNS
    )
    agreements = {}
    for record in agreement_rows:
        ident = record["id"]
        base.require(ident in history_by_id and ident not in agreements and
                     base.HEX40.fullmatch(record["first_agree_sha"]) is not None and
                     (not record["second_agree_sha"] or (
                         base.HEX40.fullmatch(record["second_agree_sha"]) is not None and
                         record["second_agree_sha"] != record["first_agree_sha"]
                     )), "invalid agreement evidence")
        agreements[ident] = record
    base.require(all(row["state"] != "agrees" or row["id"] in agreements
                     for row in state.values()),
                 "agreeing witness lacks independent SHA evidence")

    # First validate every new item against the live exact-source compiler.
    # A malformed, missing, or non-reproducing witness aborts the entire run
    # BEFORE copying any new files into the durable branch workspace.
    copies: list[tuple[Path, Path, dict[str, str]]] = []
    new_ids: set[str] = set()
    for row in newly_reported:
        ident = row["id"]
        base.require(ident not in new_ids and ident in history_by_id and
                     v2.fingerprint_id(row) == ident and
                     v2.key(row) in current, "invalid or repeated new v2 finding")
        source_row = current[v2.key(row)]
        base.require(row["occurrences"] == source_row["occurrences"] and
                     row["first_seed"] == source_row["first_seed"] and
                     row["first_profile"] == source_row["first_profile"],
                     "new v2 finding metadata does not match original report")
        new_ids.add(ident)
        source = v2.safe_witness(report, source_row["minimized_ir"],
                                 "minimized.ir")
        if ident in state:
            base.require(state[ident]["last_run_token"] == token and
                         state[ident]["state"] == "reproduces" and
                         witness_path(history_dir, ident).read_bytes() == source.read_bytes(),
                         "repeated v2 archive differs from already verified witness")
            continue
        target = witness_path(history_dir, ident)
        status, detail = classify(oracle, source, row, timeout)
        base.require(status == "reproduces",
                     f"new minimized v2 witness did not reproduce: {detail}")
        if target.exists():
            base.require(target.read_bytes() == source.read_bytes(),
                         "distinct witnesses collide at same v2 fingerprint ID")
        copies.append((source, target, row))
    # If the same run is replayed, state already holds its archived IDs.
    # Otherwise every new history key must be present in the signed triage
    # manifest; a truncated manifest must not silently omit a new archive.
    expected = {
        row["id"] for row in history
        if row["first_run"] == url and row["first_sha"] == sha
        and row["id"] not in state
    }
    base.require(expected.issubset(new_ids),
                 "new v2 issue roster omits an unarchived fingerprint")
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
    counts = {"reproduces": 0, "agrees": 0,
              "changed-mismatch": 0, "unavailable": 0}
    events = []
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
        event = None
        if status == "agrees":
            if ident not in agreements:
                agreements[ident] = {
                    "id": ident, "first_agree_sha": sha, "second_agree_sha": "",
                }
                event = "suspected-fix"
            elif not agreements[ident]["second_agree_sha"] and (
                agreements[ident]["first_agree_sha"] != sha
            ):
                agreements[ident]["second_agree_sha"] = sha
                event = "second-revision-agreement"
        elif status in ("reproduces", "changed-mismatch"):
            if ident in agreements:
                # A genuine mismatch invalidates previous agreement evidence,
                # even if an infrastructure outage occurred in between.
                if status == "reproduces":
                    event = "regression"
                else:
                    event = "changed-outcome"
                del agreements[ident]
            elif status == "changed-mismatch" and previous and (
                previous["state"] == "reproduces"
            ):
                event = "changed-outcome"
        state[ident] = {
            "id": ident, "state": status, "last_sha": sha,
            "last_run_token": token, "last_run": url,
            "detail": detail.replace("\t", " ").replace("\n", " ")[:180],
        }
        if event:
            base.require(event in EVENTS, "invalid v2 lifecycle transition")
            events.append({
                "id": ident, "event": event, "source_sha": sha,
                "run_url": url, "detail": state[ident]["detail"],
            })
    out.mkdir(parents=True, exist_ok=True)
    base.write_tsv(history_dir / "outcome_rechecks_v2.tsv", STATUS_COLUMNS,
                   sorted(state.values(), key=lambda row: row["id"]))
    base.write_tsv(history_dir / "outcome_agreements_v2.tsv", AGREEMENT_COLUMNS,
                   sorted(agreements.values(), key=lambda row: row["id"]))
    base.write_tsv(out / "outcome_events_v2.tsv", EVENT_COLUMNS, events)
    (out / "outcome_recheck_v2.md").write_text(
        "# FZ-3 v2 archived trap/return rechecks\n\n"
        f"Verified and archived new exact outcomes: {len(copies)}\n\n"
        f"Previously archived checked: {sum(counts.values())}\n\n"
        f"Exact reproductions: {counts['reproduces']}; "
        f"independently agreeing: {counts['agrees']}; "
        f"changed mismatches: {counts['changed-mismatch']}; "
        f"unavailable: {counts['unavailable']}.\n\n"
        f"Verified lifecycle transitions: {len(events)}.\n"
        "Agreement is only tentative fix evidence. Two different SHA "
        "replays are required for stronger evidence. No issue is automatically closed; "
        "unsupported code, original-reference drift, and timeouts are unavailable.\n",
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
