#!/usr/bin/env python3
"""Recheck archived FZ-3 native witnesses across compiler revisions.

Uses the real *unmodified* native-versus-original-reference oracle.
A missing fingerprint from a rotating fuzz seed is NEVER a fix signal.
The only tentative fix evidence is an executed, now-agreeing archived IR.
All exceptions / unsupported code / timeouts are "unavailable", not fixed.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import fz3_triage as base

STATUS_COLUMNS = (
    "id", "state", "last_sha", "last_run_token", "last_run",
    "agree_revisions", "first_agree_sha", "detail",
)
EVENT_COLUMNS = ("id", "event", "source_sha", "run_url", "detail")
STATES = {"reproduces", "not-reproduced", "other-wrong-value", "unavailable"}
EVENTS = {"suspected-fix", "second-revision-agreement", "regression", "changed-wrong-value"}
WRONG_VALUE = re.compile(
    r"WRONG_VALUE a=(-?[0-9]+) b=(-?[0-9]+) expected=([0-9]+) observed=([0-9]+)\s*\Z"
)
SAFE_ID = re.compile(r"[a-f0-9]{20}\Z")


def safe_detail(s: str) -> str:
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")[:180]


def archived_path(directory: Path, fp: str) -> Path:
    base.require(SAFE_ID.fullmatch(fp) is not None, "invalid fingerprint ID")
    return directory / "witnesses" / f"{fp}.ir"


def run_oracle(oracle: Path, witness: Path, fp: dict[str, str],
               timeout: int) -> tuple[str, str]:
    args = [
        str(oracle), str(witness), "--a", fp["a"], "--b", fp["b"],
        "--sink", fp["sink"],
    ]
    if fp["mode"] != "raw":
        args += ["--optimize", fp["mode"]]
    try:
        result = subprocess.run(
            args, check=False, capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unavailable", safe_detail(str(exc))
    if result.returncode == 1:
        return "not-reproduced", "real native output agrees with independent reference"
    if result.returncode != 0:
        return "unavailable", safe_detail(result.stderr or
                                           f"oracle exit {result.returncode}")
    match = WRONG_VALUE.fullmatch(result.stdout.strip())
    if not match:
        return "unavailable", "oracle reported malformed wrong-value output"
    a, b, expected, observed = match.groups()
    if (a, b) != (fp["a"], fp["b"]) or expected != fp["expected"]:
        return "unavailable", "original-reference or triggering-input mismatch"
    if observed == fp["actual"]:
        return "reproduces", "exact pinned native wrong-value signature still reproduces"
    return "other-wrong-value", (
        f"native result changed to {observed}; still differs from {expected}"
    )


def recheck(history_dir: Path, campaign: Path, triage_dir: Path,
            out: Path, oracle: Path, sha: str, run_token: str,
            run_url: str, limit: int = 16, timeout: int = 40
            ) -> dict[str, int]:
    base.require(base.HEX40.fullmatch(sha) is not None, "invalid full source SHA")
    base.require(base.RUN_TOKEN.fullmatch(run_token) is not None, "invalid run token")
    base.require(run_url.startswith("https://github.com/bingordo/compiler-ci/actions/runs/"),
                 "unexpected CI run URL")
    base.require(1 <= limit <= 128 and 1 <= timeout <= 180, "unsafe recheck budgets")
    rows, digest = base.verify_campaign(campaign, sha)
    keys = {base.key_tuple(row): row for row in rows}
    history = base.read_tsv(history_dir / "fingerprints.tsv", base.HISTORY_COLUMNS)
    processed = base.read_tsv(history_dir / "processed_runs.tsv", base.PROCESSED_COLUMNS)
    matched_run = [row for row in processed if row["run_token"] == run_token]
    base.require(len(matched_run) == 1 and matched_run[0]["source_sha"] == sha and
                 matched_run[0]["report_digest"] == digest and
                 matched_run[0]["run_url"] == run_url,
                 "triage must validate and register this exact native report first")
    new = base.read_tsv(triage_dir / "new.tsv", base.NEW_COLUMNS)
    state_path = history_dir / "rechecks.tsv"
    previous = base.read_tsv(state_path, STATUS_COLUMNS)
    by_id = {}
    for record in previous:
        base.require(record["id"] not in by_id, "duplicate lifecycle state")
        base.require(record["state"] in STATES, "invalid lifecycle state")
        by_id[record["id"]] = record
    source_ids = set()
    for row in history:
        base.require(row["id"] == base.fingerprint_id(row), "invalid history signature")
        base.require(row["id"] not in source_ids, "duplicate fingerprint history")
        source_ids.add(row["id"])
    base.require(set(by_id).issubset(source_ids), "orphan lifecycle status")
    out.mkdir(parents=True, exist_ok=True)
    events = []
    for row in new:
        base.require(row["id"] in source_ids, "new key missing from validated history")
        base.require(row["id"] == base.fingerprint_id(row), "new fingerprint hash mismatch")
        base.require(base.key_tuple(row) in keys, "new fingerprint not in campaign")
        original = keys[base.key_tuple(row)]
        dst = archived_path(history_dir, row["id"])
        source = base.safe_artifact(campaign, original["minimized_ir"], "minimized.ir")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            base.require(dst.read_bytes() == source.read_bytes(),
                         "new signature collides with distinct archived witness")
        else:
            shutil.copyfile(source, dst)
        base.require(row["id"] not in by_id, "already-known signature marked as new")
        by_id[row["id"]] = {
            "id": row["id"], "state": "reproduces",
            "last_sha": sha, "last_run_token": run_token, "last_run": run_url,
            "agree_revisions": "0", "first_agree_sha": "",
            "detail": "first witnessed in campaign and minimized",
        }

    checked = 0
    unavailable = 0
    not_reproduced = 0
    for row in history:
        fp_id = row["id"]
        if fp_id in {x["id"] for x in new}:
            continue  # Do not claim a fresh finding is fixed on the same run.
        witness = archived_path(history_dir, fp_id)
        if checked >= limit:
            break
        checked += 1
        if not witness.is_file():
            state, detail = "unavailable", "archived minimized IR unavailable"
        else:
            state, detail = run_oracle(oracle, witness, row, timeout)
        old = by_id.get(fp_id)
        if old is not None and old["last_run_token"] == run_token:
            continue  # Duplicate processing must never produce another event.
        old_state = old["state"] if old else "unavailable"
        old_agreement = int(old["agree_revisions"]) if old else 0
        next_agreement = 0
        first_agree_sha = ""
        event = None
        if state == "not-reproduced":
            not_reproduced += 1
            if old_state != "not-reproduced":
                next_agreement = 1
                first_agree_sha = sha
                event = "suspected-fix"
            else:
                next_agreement = old_agreement
                first_agree_sha = old["first_agree_sha"] if old else sha
                if old["last_sha"] != sha:
                    next_agreement += 1
                    if next_agreement == 2:
                        event = "second-revision-agreement"
        elif state == "unavailable":
            unavailable += 1
            if old_state == "not-reproduced" and old is not None:
                # Do not erase prior clean evidence on infrastructure failure.
                next_agreement = old_agreement
                first_agree_sha = old["first_agree_sha"]
        elif state == "reproduces" and old_state == "not-reproduced":
            event = "regression"
        elif state == "other-wrong-value" and old_state not in (
            "other-wrong-value", "unavailable"
        ):
            event = "changed-wrong-value"

        by_id[fp_id] = {
            "id": fp_id, "state": state, "last_sha": sha,
            "last_run_token": run_token, "last_run": run_url,
            "agree_revisions": str(next_agreement),
            "first_agree_sha": first_agree_sha,
            "detail": safe_detail(detail),
        }
        if event is not None:
            base.require(event in EVENTS, "unknown lifecycle event")
            events.append({
                "id": fp_id, "event": event, "source_sha": sha,
                "run_url": run_url, "detail": safe_detail(detail),
            })
    base.write_tsv(state_path, STATUS_COLUMNS,
                   sorted(by_id.values(), key=lambda x: x["id"]))
    base.write_tsv(out / "events.tsv", EVENT_COLUMNS, events)
    result = dict(
        archived=len(new), checked=checked,
        suspected=not_reproduced, unavailable=unavailable, transitions=len(events),
        tracked=len(by_id),
    )
    (out / "lifecycle.md").write_text(
        "# FZ-3 archived-witness rechecks\n\n"
        f"Source: \`{sha}\`; [CI run]({run_url})\n\n"
        f"Archived new minimized witnesses: **{result['archived']}**\n\n"
        f"Old fingerprints replayed: **{checked}** (limit {limit})\n\n"
        f"Currently agreeing with reference: **{not_reproduced}** "
        "(possible fix, **not** definitive)\n\n"
        f"Unavailable/failed-to-execute: **{unavailable}** "
        "(never considered fixed)\n\n"
        f"Lifecycle transitions: **{len(events)}**\n\n"
        "Rotating-seed absence is NOT used as a fix signal. No issue is "
        "automatically closed; a repeated exact mismatch after a previous "
        "verified agreement is marked as a possible regression.\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--history-dir", required=True, type=Path)
    p.add_argument("--campaign", required=True, type=Path)
    p.add_argument("--triage-dir", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--oracle", required=True, type=Path)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--run-token", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--limit", type=int, default=16)
    p.add_argument("--timeout", type=int, default=40)
    args = p.parse_args()
    try:
        print(recheck(args.history_dir, args.campaign, args.triage_dir, args.out,
                      args.oracle, args.source_sha, args.run_token, args.run_url,
                      args.limit, args.timeout))
        return 0
    except (OSError, ValueError, KeyError) as err:
        print(f"FZ-3 archived witness recheck rejected: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
