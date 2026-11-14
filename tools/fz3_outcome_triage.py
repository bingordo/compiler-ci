#!/usr/bin/env python3
"""Validate and reconcile versioned FZ-3 native trap/return fingerprints.

V2 is independent of the existing wrong-value v1 ledger. Only an authentic,
non-injected campaign already registered by v1 triage is admissible. No
historical state or issue is changed for rejected or repeated submissions.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path, PurePosixPath

import fz3_triage as base

V2_COLUMNS = (
    "schema_version", "mode", "sink", "a", "b", "expected", "actual",
    "occurrences", "first_profile", "first_seed", "probes",
    "removed_instructions", "before_ir", "minimized_ir",
)
KEY = ("mode", "sink", "a", "b", "expected", "actual")
HISTORY_COLUMNS = (
    "id", *KEY, "first_sha", "first_run", "last_sha", "last_run",
    "runs", "observations", "first_profile", "first_seed",
)
PROCESSED_COLUMNS = base.PROCESSED_COLUMNS
NEW_COLUMNS = ("id", *KEY, "occurrences", "first_profile",
               "first_seed", "issue_body")
OUTCOME = re.compile(r"(?:sigfpe|value:(?:0|[1-9][0-9]*))\Z")
FINDING = re.compile(r"outcome-[0-9]{3}\Z")
RUN_URL = re.compile(
    r"https://github[.]com/bingordo/compiler-ci/actions/runs/[0-9]+\Z"
)


def fingerprint_id(row: dict[str, str]) -> str:
    canonical = "\0".join(("FZ3-native-outcome-v2", *(row[k] for k in KEY)))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:20]


def key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[k] for k in KEY)


def valid_outcome(value: str) -> bool:
    if OUTCOME.fullmatch(value) is None:
        return False
    return value == "sigfpe" or int(value.removeprefix("value:")) <= 2**32 - 1


def safe_witness(root: Path, relative: str, filename: str) -> Path:
    parts = PurePosixPath(relative)
    base.require(not parts.is_absolute() and len(parts.parts) == 2 and
                 FINDING.fullmatch(parts.parts[0]) is not None and
                 parts.parts[1] == filename,
                 "unsafe v2 witness path")
    candidate = root.joinpath(*parts.parts)
    base.require(candidate.is_file() and
                 candidate.resolve().is_relative_to(root.resolve()),
                 "missing or escaping v2 witness")
    return candidate


def verify_campaign(report: Path, sha: str) -> tuple[list[dict[str, str]], str]:
    # Reusing v1's verifier also rejects self-test injection and requires
    # the unchanged v1 summary/TSV contract to remain valid.
    base.verify_campaign(report, sha)
    summary = base.read_summary(report / "summary.txt")
    base.require((report / "outcome_fingerprints_v2.tsv").is_file(),
                 "missing versioned outcome report")
    rows = base.read_tsv(report / "outcome_fingerprints_v2.tsv", V2_COLUMNS)
    base.require(base.UINT.fullmatch(summary.get("outcome_unique_failures", "")) is not None
                 and int(summary["outcome_unique_failures"]) == len(rows),
                 "outcome count does not match v2 report")
    base.require(base.UINT.fullmatch(summary.get("outcome_duplicates", "")) is not None,
                 "missing outcome duplicate count")
    base.require(base.UINT.fullmatch(summary.get("evaluations", "")) is not None,
                 "missing bounded native evaluation count")
    base.require(base.UINT.fullmatch(summary.get("max_unique", "")) is not None,
                 "missing bounded unique failure budget")
    base.require(len(rows) + int(summary["unique_failures"]) <=
                 int(summary["max_unique"]) <= 16,
                 "outcome findings exceed bounded unique budget")
    total_observations = 0
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        base.require(row["schema_version"] == "2", "unexpected outcome schema version")
        base.require(row["mode"] in ("raw", "disabled", "scalar", "cost-aware"),
                     "unknown optimizer policy")
        base.require(row["sink"] in ("gas", "elf"), "unknown native sink")
        for name in ("a", "b"):
            base.require(base.SINT.fullmatch(row[name]) is not None and
                         -(2**31) <= int(row[name]) <= 2**31 - 1,
                         "noncanonical i32 input")
        base.require(valid_outcome(row["expected"]) and
                     valid_outcome(row["actual"]) and
                     row["expected"] != row["actual"],
                     "invalid or matching outcome fingerprint")
        for name in ("occurrences", "first_seed", "probes", "removed_instructions"):
            base.require(base.UINT.fullmatch(row[name]) is not None,
                         "noncanonical outcome count")
        base.require(1 <= int(row["occurrences"]) <= 256 and
                     int(row["probes"]) <= 128,
                     "unbounded outcome observations or minimization")
        total_observations += int(row["occurrences"])
        base.require(row["first_profile"] in ("signed-div-alias", "signed-rem-alias"),
                     "unknown trapping profile")
        k = key(row)
        base.require(k not in seen, "duplicate v2 outcome fingerprint")
        seen.add(k)
        before = safe_witness(report, row["before_ir"], "before.ir")
        minimized = safe_witness(report, row["minimized_ir"], "minimized.ir")
        base.require(before.parent == minimized.parent and
                     (before.parent / "compiled.ir").is_file() and
                     (before.parent / "replay.txt").is_file(),
                     "incomplete or mismatched outcome replay artifacts")
        replay = (before.parent / "replay.txt").read_text(encoding="utf-8")
        for pin in (
            f"source_sha={sha}", f"mode={row['mode']}", f"sink={row['sink']}",
            f"expected_outcome={row['expected']}",
            f"observed_outcome={row['actual']}",
            f"--expected-outcome {row['expected']}",
            f"--observed-outcome {row['actual']}",
        ):
            base.require(pin in replay, "replay does not match pinned outcome")
    base.require(total_observations <= int(summary["evaluations"]),
                 "more outcome observations than native evaluations")
    digest = hashlib.sha256(
        (report / "summary.txt").read_bytes() + b"\0" +
        (report / "outcome_fingerprints_v2.tsv").read_bytes()
    ).hexdigest()
    return rows, digest


def validate_previous(history: list[dict[str, str]],
                      processed: list[dict[str, str]]) -> None:
    ids: set[str] = set()
    keys: set[tuple[str, ...]] = set()
    runs: set[str] = set()
    for row in history:
        base.require(row["id"] == fingerprint_id(row) and row["id"] not in ids
                     and key(row) not in keys, "corrupt v2 fingerprint history")
        ids.add(row["id"])
        keys.add(key(row))
        base.require(all(valid_outcome(row[name]) for name in ("expected", "actual"))
                     and row["expected"] != row["actual"], "invalid historical outcome")
        for field in ("runs", "observations", "first_seed"):
            base.require(base.UINT.fullmatch(row[field]) is not None,
                         "malformed historical numeric field")
        base.require(int(row["runs"]) > 0 and int(row["observations"]) > 0,
                     "empty historical fingerprint")
        for field in ("first_sha", "last_sha"):
            base.require(base.HEX40.fullmatch(row[field]) is not None,
                         "bad historical source SHA")
        for field in ("first_run", "last_run"):
            base.require(RUN_URL.fullmatch(row[field]) is not None,
                         "bad historical run URL")
        base.require(base.SAFE_LABEL.fullmatch(row["first_profile"]) is not None,
                     "bad historical profile")
    for row in processed:
        base.require(base.RUN_TOKEN.fullmatch(row["run_token"]) is not None and
                     row["run_token"] not in runs, "invalid/duplicate historical run")
        runs.add(row["run_token"])
        base.require(base.HEX40.fullmatch(row["source_sha"]) is not None and
                     re.fullmatch(r"[a-f0-9]{64}", row["report_digest"]) is not None and
                     RUN_URL.fullmatch(row["run_url"]) is not None,
                     "malformed processed v2 run")


def issue_body(row: dict[str, str], ident: str, sha: str, url: str) -> str:
    return (
        f"<!-- FZ3-OUTCOME:{ident} -->\n"
        "# FZ-3 native trap/return discrepancy\n\n"
        "Independent original TypeId semantics and actual native GAS/direct-ELF "
        "execution disagree. This is a real non-injected symptom, not a diagnosed "
        "compiler root cause.\n\n"
        f"Fingerprint: FZ3-OUTCOME:{ident}\n\n"
        f"- Policy/sink: {row['mode']}/{row['sink']}\n"
        f"- Inputs: a={row['a']}, b={row['b']}\n"
        f"- Reference outcome: {row['expected']}\n"
        f"- Native outcome: {row['actual']}\n"
        f"- Recipe: {row['first_profile']}, seed {row['first_seed']}\n"
        f"- Exact source: {sha}\n"
        f"- Original run: {url}\n\n"
        "Download the run's fz3-nightly-native-differential-replay artifact; "
        f"replay {row['minimized_ir']} with the exact pinned flags in replay.txt. "
        "No issue is automatically closed on absence from rotating seeds.\n"
    )


def reconcile(report: Path, history_dir: Path, out: Path,
              sha: str, token: str, url: str) -> dict[str, int]:
    base.require(base.HEX40.fullmatch(sha) is not None, "invalid exact source SHA")
    base.require(base.RUN_TOKEN.fullmatch(token) is not None, "invalid run token")
    base.require(RUN_URL.fullmatch(url) is not None and
                 url.endswith("/" + token.split(".")[0]), "invalid run URL/token")
    rows, digest = verify_campaign(report, sha)
    # V1 must have accepted precisely this run before any V2 history changes.
    _, v1digest = base.verify_campaign(report, sha)
    registered = base.read_tsv(history_dir / "processed_runs.tsv",
                               base.PROCESSED_COLUMNS)
    matches = [r for r in registered if r["run_token"] == token]
    base.require(len(matches) == 1 and matches[0]["run_url"] == url and
                 matches[0]["source_sha"] == sha and
                 matches[0]["report_digest"] == v1digest,
                 "v1 native campaign must first be validated and registered")
    history_path = history_dir / "outcome_fingerprints_v2.tsv"
    processed_path = history_dir / "outcome_processed_runs_v2.tsv"
    base.require(history_path.exists() == processed_path.exists(),
                 "partial v2 historical initialization")
    history = base.read_tsv(history_path, HISTORY_COLUMNS)
    processed = base.read_tsv(processed_path, PROCESSED_COLUMNS)
    validate_previous(history, processed)
    seen_runs = {r["run_token"]: r for r in processed}
    out.mkdir(parents=True, exist_ok=True)
    if token in seen_runs:
        old = seen_runs[token]
        base.require(old["run_url"] == url and old["source_sha"] == sha and
                     old["report_digest"] == digest,
                     "same run replayed with changed outcome report")
        base.write_tsv(out / "outcome_new_v2.tsv", NEW_COLUMNS, [])
        (out / "outcome_status_v2.txt").write_text(
            "processed=0\nnew=0\nknown=0\nreplayed=1\n", encoding="utf-8")
        return {"processed": 0, "new": 0, "known": 0, "replayed": 1}

    by_key = {key(row): dict(row) for row in history}
    new: list[dict[str, str]] = []
    known = 0
    for row in rows:
        k = key(row)
        if k in by_key:
            prior = by_key[k]
            prior["last_sha"] = sha
            prior["last_run"] = url
            prior["runs"] = str(int(prior["runs"]) + 1)
            prior["observations"] = str(int(prior["observations"]) + int(row["occurrences"]))
            known += 1
            continue
        ident = fingerprint_id(row)
        by_key[k] = {
            "id": ident, **{field: row[field] for field in KEY},
            "first_sha": sha, "first_run": url,
            "last_sha": sha, "last_run": url,
            "runs": "1", "observations": row["occurrences"],
            "first_profile": row["first_profile"], "first_seed": row["first_seed"],
        }
        issue = f"outcome-issue-{ident}.md"
        (out / issue).write_text(issue_body(row, ident, sha, url), encoding="utf-8")
        new.append({
            "id": ident, **{field: row[field] for field in KEY},
            "occurrences": row["occurrences"],
            "first_profile": row["first_profile"],
            "first_seed": row["first_seed"], "issue_body": issue,
        })
    processed.append({
        "run_token": token, "run_url": url, "source_sha": sha,
        "report_digest": digest,
    })
    base.write_tsv(history_path, HISTORY_COLUMNS,
                   sorted(by_key.values(), key=key))
    base.write_tsv(processed_path, PROCESSED_COLUMNS,
                   sorted(processed, key=lambda r: tuple(map(int, r["run_token"].split(".")))))
    base.write_tsv(out / "outcome_new_v2.tsv", NEW_COLUMNS, new)
    (out / "outcome_status_v2.txt").write_text(
        f"processed=1\nnew={len(new)}\nknown={known}\nreplayed=0\n",
        encoding="utf-8")
    (out / "outcome_triage_v2.md").write_text(
        "# FZ-3 v2 native trap/return triage\n\n"
        f"Exact source: {sha}; CI run: {url}\n\n"
        f"New: {len(new)}; known: {known}; examined: {len(rows)}.\n"
        "V1 wrong-value history is unchanged. Outcome claims require "
        "exact-replay validation before archiving and lifecycle reporting.\n",
        encoding="utf-8")
    return {"processed": 1, "new": len(new), "known": known, "replayed": 0}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("campaign", "history-dir", "out"):
        p.add_argument("--" + flag, required=True, type=Path)
    for flag in ("source-sha", "run-token", "run-url"):
        p.add_argument("--" + flag, required=True)
    a = p.parse_args()
    try:
        print(reconcile(a.campaign, a.history_dir, a.out,
                        a.source_sha, a.run_token, a.run_url))
    except (ValueError, KeyError, OSError) as error:
        print(f"FZ-3 v2 outcome triage rejected: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
