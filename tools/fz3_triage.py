#!/usr/bin/env python3
"""Cross-run FZ-3 native wrong-value triage. Standard-library only.

Reads a *validated, non-injected* campaign report, compares exact fingerprints
against durable history on a separate Git branch, and emits new/known findings
plus reproducible issue drafts. Does NOT contact GitHub or execute input IR.
The workflow owns issue deduplication and atomic git publication.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path, PurePosixPath

CAMPAIGN_COLUMNS = (
    "mode", "sink", "a", "b", "expected", "actual", "occurrences",
    "first_profile", "first_seed", "probes", "removed_instructions",
    "before_ir", "minimized_ir",
)
KEY = ("mode", "sink", "a", "b", "expected", "actual")
HISTORY_COLUMNS = (
    "id", *KEY, "first_sha", "first_run", "last_sha", "last_run",
    "runs", "observations", "first_profile", "first_seed",
)
PROCESSED_COLUMNS = ("run_token", "run_url", "source_sha", "report_digest")
NEW_COLUMNS = ("id", *KEY, "occurrences", "first_profile", "first_seed", "issue_body")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
RUN_TOKEN = re.compile(r"[0-9]+\.[0-9]+\Z")
SAFE_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
UINT = re.compile(r"(0|[1-9][0-9]*)\Z")
SINT = re.compile(r"-?(0|[1-9][0-9]*)\Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_tsv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        require(tuple(reader.fieldnames or ()) == columns,
                f"invalid TSV schema: {path}")
        rows = list(reader)
    for row in rows:
        require(None not in row and all(value is not None for value in row.values()),
                f"invalid TSV row: {path}")
    return rows


def write_tsv(path: Path, columns: tuple[str, ...],
              rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t",
                                lineterminator="\n", extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def read_summary(path: Path) -> dict[str, str]:
    require(path.is_file(), f"missing campaign summary: {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        require(bool(sep) and name and name not in result,
                f"invalid or duplicate summary key: {line!r}")
        result[name] = value
    return result


def fingerprint_id(row: dict[str, str]) -> str:
    canonical = "\0".join(("FZ3-native-wrong-value-v1", *(row[key] for key in KEY)))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:20]


def key_tuple(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(row[key] for key in KEY)


def safe_artifact(root: Path, raw: str, expected: str) -> Path:
    rel = PurePosixPath(raw)
    require(not rel.is_absolute() and rel.parts and
            all(part not in ("..", ".", "") for part in rel.parts) and
            rel.name == expected,
            f"invalid artifact path {raw!r}")
    path = root.joinpath(*rel.parts)
    require(path.is_file(), f"missing artifact: {path}")
    return path


def verify_campaign(root: Path, source_sha: str
                    ) -> tuple[list[dict[str, str]], str]:
    summary = read_summary(root / "summary.txt")
    require(summary.get("injected") == "false",
            "injected campaigns are NEVER eligible for the genuine history")
    require(summary.get("source_sha") == source_sha,
            "campaign source SHA differs from checked-out source SHA")
    require(summary.get("status") in ("no-mismatch", "native-wrong-value"),
            "unsupported/unfinished campaign status")
    rows = read_tsv(root / "fingerprints.tsv", CAMPAIGN_COLUMNS)
    require(len(rows) == int(summary["unique_failures"]),
            "campaign summary unique failure count does not match TSV")
    require((bool(rows) and summary["status"] == "native-wrong-value") or
            (not rows and summary["status"] == "no-mismatch"),
            "campaign mismatch status does not match rows")
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        require(row["mode"] in ("raw", "disabled", "scalar", "cost-aware"),
                "unknown optimizer mode")
        require(row["sink"] in ("gas", "elf"), "unknown backend sink")
        require(SINT.fullmatch(row["a"]) is not None and
                SINT.fullmatch(row["b"]) is not None and
                -2**31 <= int(row["a"]) < 2**31 and
                -2**31 <= int(row["b"]) < 2**31, "invalid i32 input")
        for field in ("expected", "actual"):
            require(UINT.fullmatch(row[field]) is not None and
                    int(row[field]) <= 2**32 - 1,
                    f"invalid u32 {field}")
        require(row["actual"] != row["expected"],
                "matching native result is not a wrong-value fingerprint")
        for field in ("occurrences", "first_seed", "probes", "removed_instructions"):
            require(UINT.fullmatch(row[field]) is not None,
                    f"invalid nonnegative integer {field}")
        require(int(row["occurrences"]) > 0, "zero-observation finding")
        require(SAFE_LABEL.fullmatch(row["first_profile"]) is not None,
                "unsafe profile label")
        k = key_tuple(row)
        require(k not in seen, "duplicate exact fingerprint in one run")
        seen.add(k)
        before = safe_artifact(root, row["before_ir"], "before.ir")
        minimized = safe_artifact(root, row["minimized_ir"], "minimized.ir")
        require(before.parent == minimized.parent,
                "original and reduced IR belong to different findings")
        require((before.parent / "compiled.ir").is_file() and
                (before.parent / "replay.txt").is_file(),
                "finding missing compiled IR or replay instructions")
    blob = (root / "summary.txt").read_bytes() + b"\0" + \
           (root / "fingerprints.tsv").read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    return rows, digest


def issue_body(row: dict[str, str], key_id: str, source_sha: str,
               run_url: str) -> str:
    return (
        f"<!-- FZ3-FP:{key_id} -->\n"
        f"## FZ-3: new native wrong-value fingerprint\n\n"
        f"This is a **genuine, non-injected** native-vs-independent-reference "
        f"mismatch, not yet a diagnosed compiler root cause.\n\n"
        f"Fingerprint: \`FZ3-FP:{key_id}\`\n\n"
        f"- Optimizer: \`{row['mode']}\`; sink: \`{row['sink']}\`\n"
        f"- Inputs: \`a={row['a']}\`, \`b={row['b']}\`\n"
        f"- Reference: \`{row['expected']}\`; native: \`{row['actual']}\`\n"
        f"- First profile: \`{row['first_profile']}\`, seed \`{row['first_seed']}\`\n"
        f"- Exact compiler SHA: \`{source_sha}\`\n"
        f"- [First observed CI run]({run_url})\n\n"
        f"Download the \`fz3-nightly-native-differential-replay\` artifact "
        f"from the CI run. It contains \`{row['minimized_ir']}\`, its "
        f"\`replay.txt\`, the original \`{row['before_ir']}\`, and "
        f"\`compiled.ir\`. **Reproduce the exact pinned wrong-value "
        f"signature** before attributing the bug to any compiler pass.\n\n"
        f"Subsequent occurrences are tracked in the durable "
        f"\`fz3-native-fingerprint-history\` branch without duplicate issues.\n"
    )


def triage(report: Path, history_dir: Path, output: Path,
           source_sha: str, run_token: str, run_url: str) -> dict[str, int]:
    require(HEX40.fullmatch(source_sha) is not None, "source must be full lower-case SHA")
    require(RUN_TOKEN.fullmatch(run_token) is not None, "invalid GitHub run token")
    require(run_url.startswith("https://github.com/bingordo/compiler-ci/actions/runs/"),
            "unexpected CI run URL")
    rows, digest = verify_campaign(report, source_sha)
    history_path = history_dir / "fingerprints.tsv"
    processed_path = history_dir / "processed_runs.tsv"
    history = read_tsv(history_path, HISTORY_COLUMNS)
    processed = read_tsv(processed_path, PROCESSED_COLUMNS)
    seen_ids = set()
    prior = {}
    for row in history:
        require(row["id"] == fingerprint_id(row), "history fingerprint hash mismatch")
        require(row["id"] not in seen_ids, "duplicate history fingerprint")
        seen_ids.add(row["id"])
        prior[key_tuple(row)] = row
    require(len(prior) == len(history), "ambiguous persistent fingerprint history")
    prior_runs = {}
    for row in processed:
        require(row["run_token"] not in prior_runs, "duplicate processed run")
        prior_runs[row["run_token"]] = row
    output.mkdir(parents=True, exist_ok=True)
    if run_token in prior_runs:
        old = prior_runs[run_token]
        require(old["report_digest"] == digest and
                old["source_sha"] == source_sha and old["run_url"] == run_url,
                "same CI run reprocessed with different report content")
        write_tsv(output / "new.tsv", NEW_COLUMNS, [])
        (output / "triage.md").write_text(
            "# FZ-3 triage\n\nIdentical CI run already processed: no alerts.\n",
            encoding="utf-8")
        (output / "status.txt").write_text(
            "processed=0\nnew=0\nknown=0\nreplayed=1\n", encoding="utf-8")
        return {"new": 0, "known": 0, "processed": 0, "replayed": 1}

    fresh = []
    known = 0
    for row in rows:
        key = key_tuple(row)
        if key in prior:
            existing = prior[key]
            existing["last_sha"] = source_sha
            existing["last_run"] = run_url
            existing["runs"] = str(int(existing["runs"]) + 1)
            existing["observations"] = str(
                int(existing["observations"]) + int(row["occurrences"]))
            known += 1
        else:
            key_id = fingerprint_id(row)
            prior[key] = {
                "id": key_id, **{field: row[field] for field in KEY},
                "first_sha": source_sha, "first_run": run_url,
                "last_sha": source_sha, "last_run": run_url,
                "runs": "1", "observations": row["occurrences"],
                "first_profile": row["first_profile"], "first_seed": row["first_seed"],
            }
            issue_file = f"issue-{key_id}.md"
            (output / issue_file).write_text(
                issue_body(row, key_id, source_sha, run_url), encoding="utf-8")
            fresh.append({
                "id": key_id, **{field: row[field] for field in KEY},
                "occurrences": row["occurrences"],
                "first_profile": row["first_profile"],
                "first_seed": row["first_seed"],
                "issue_body": issue_file,
            })
    processed.append({
        "run_token": run_token, "run_url": run_url,
        "source_sha": source_sha, "report_digest": digest,
    })
    write_tsv(history_path, HISTORY_COLUMNS,
              sorted(prior.values(), key=lambda x: key_tuple(x)))
    write_tsv(processed_path, PROCESSED_COLUMNS,
              sorted(processed, key=lambda x: tuple(map(int, x["run_token"].split(".")))))
    write_tsv(output / "new.tsv", NEW_COLUMNS, fresh)
    (output / "triage.md").write_text(
        f"# FZ-3 persistent native differential triage\n\n"
        f"Source: \`{source_sha}\`; [CI run]({run_url})\n\n"
        f"- New exact fingerprints: **{len(fresh)}**\n"
        f"- Known recurring fingerprints: **{known}**\n"
        f"- Reported in-run unique fingerprints: **{len(rows)}**\n"
        f"- Injected faults: **not permitted**\n\n"
        f"New fingerprints have issue drafts and complete pinned IR artifacts "
        f"in the source campaign report. The history records exact symptoms, "
        f"not inferred shared root causes.\n", encoding="utf-8")
    (output / "status.txt").write_text(
        f"processed=1\nnew={len(fresh)}\nknown={known}\nreplayed=0\n",
        encoding="utf-8")
    return {"new": len(fresh), "known": known, "processed": 1, "replayed": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--history-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-token", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    try:
        print(triage(args.campaign, args.history_dir, args.out,
                     args.source_sha, args.run_token, args.run_url))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"FZ-3 triage rejected report: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
