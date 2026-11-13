#!/usr/bin/env python3
"""Validate the permanent AL-03 TypeId concrete/Z3 regression inventory.

Every AL-03 test must be explicitly accounted for. The marker of each
successful ignored proof test is an exact, reviewed contract containing
changed optimizer transactions, UNSAT and SAT negative-control counts.
Do not infer success merely from a test name or a zero exit code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


class ManifestError(ValueError):
    pass


_TEST = re.compile(
    r"(?m)^#\[test\]\s*(#\[ignore[^\n]*\]\s*)?fn\s+(al03_[a-z0-9_]+)\s*\("
)
_CONCRETE_SUCCESS = re.compile(
    r"(?m)^test alive_lite_typeid_wide::(al03_[a-z0-9_]+) \.\.\. ok$"
)
_PROOF_RECORD = re.compile(
    r"(?m)^test alive_lite_typeid_wide::(al03_[a-z0-9_]+)"
    r" \.\.\. (AL03_[^\r\n]+)$"
)
_SUCCESS_SUMMARY = re.compile(r"(?m)^test result: ok\. +[1-9]\d* passed;")


def _unique(items: list[str], what: str) -> None:
    if len(items) != len(set(items)):
        raise ManifestError(f"Duplicate {what}")


def validate(
    manifest: dict[str, Any], source: str, concrete_log: str,
    proof_log: str, source_sha: str,
) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        raise ManifestError("Unsupported AL-03 manifest schema")
    suites = manifest.get("suites")
    if not isinstance(suites, list) or not suites:
        raise ManifestError("AL-03 manifest is empty")

    ordinary = [s.get("ordinary") for s in suites]
    proofs = [s.get("proof") for s in suites]
    markers = [s.get("evidence") for s in suites]
    if any(
        not isinstance(name, str) or not re.fullmatch(r"al03_[a-z0-9_]+", name)
        for name in ordinary + proofs
    ):
        raise ManifestError("Missing or invalid concrete/Z3 regression test name")
    if any(
        not isinstance(marker, str) or not re.fullmatch(r"AL03_[A-Z0-9_]+ .+", marker)
        for marker in markers
    ):
        raise ManifestError("Missing or invalid AL-03 formal evidence marker")
    _unique(ordinary, "concrete regression")
    _unique(proofs, "formal proof regression")
    _unique(markers, "formal proof marker")
    if set(ordinary) & set(proofs):
        raise ManifestError("A concrete regression cannot also be a Z3 test")

    # A newly added, removed or renamed test cannot silently pass an old
    # manifest. All AL-03 tests in the compiled source must be accounted for.
    actual = [(match.group(2), bool(match.group(1))) for match in _TEST.finditer(source)]
    _unique([name for name, _ in actual], "source AL-03 test")
    actual_ordinary = {name for name, ignored in actual if not ignored}
    actual_proofs = {name for name, ignored in actual if ignored}
    if actual_ordinary != set(ordinary) or actual_proofs != set(proofs):
        raise ManifestError(
            "AL-03 source/manifest test drift: "
            f"new_concrete={sorted(actual_ordinary - set(ordinary))}, "
            f"missing_concrete={sorted(set(ordinary) - actual_ordinary)}, "
            f"new_proofs={sorted(actual_proofs - set(proofs))}, "
            f"missing_proofs={sorted(set(proofs) - actual_proofs)}"
        )

    if not _SUCCESS_SUMMARY.search(concrete_log):
        raise ManifestError("Incomplete or failed concrete test log")
    if not _SUCCESS_SUMMARY.search(proof_log):
        raise ManifestError("Incomplete or failed external Z3 proof log")

    passed_concrete = _CONCRETE_SUCCESS.findall(concrete_log)
    _unique(passed_concrete, "executed concrete AL-03 test")
    if set(passed_concrete) != set(ordinary):
        raise ManifestError(
            f"Concrete AL-03 coverage mismatch: missing="
            f"{sorted(set(ordinary)-set(passed_concrete))}, "
            f"unmanifested={sorted(set(passed_concrete)-set(ordinary))}"
        )

    seen_proofs = _PROOF_RECORD.findall(proof_log)
    _unique([name for name, _ in seen_proofs], "executed AL-03 SMT test")
    if set(name for name, _ in seen_proofs) != set(proofs):
        raise ManifestError("Missing or unmanifested AL-03 SMT proof output")

    measured: list[dict[str, Any]] = []
    for suite in suites:
        name, expected = suite["proof"], suite["evidence"]
        test_line = f"test alive_lite_typeid_wide::{name} ... {expected}"
        # Rust --nocapture prints eprintln! output on the same test line
        # and its final "ok" on the following line.
        success = re.compile(re.escape(test_line) + r"\r?\nok(?:\r?\n|$)")
        if len(success.findall(proof_log)) != 1:
            raise ManifestError(
                f"AL-03 proof was not passed with exact reviewed evidence: {name}"
            )
        props = dict(
            item.split("=", 1) for item in expected.split()[1:]
            if "=" in item
        )
        if int(props.get("unsat", 0)) < 1:
            raise ManifestError(f"Missing actual UNSAT production proof count: {name}")
        sat = sum(int(v) for k, v in props.items() if k.startswith("sat_"))
        if sat < 1:
            raise ManifestError(f"Missing SAT negative mutant count: {name}")
        measured.append({
            "ordinary": suite["ordinary"],
            "proof": name,
            "marker": expected.split()[0],
            "real_unsat": int(props["unsat"]),
            "sat_negatives": sat,
            "independent_concrete_cases": int(props.get("concrete", 0)),
        })

    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ManifestError("An exact 40-digit source revision is required")

    return {
        "scope": "AL-03 exact-width TypeId",
        "source_sha": source_sha,
        "suites": len(suites),
        "concrete_tests": len(ordinary),
        "external_z3_tests": len(proofs),
        "real_unsat_witnesses": sum(x["real_unsat"] for x in measured),
        "sat_negative_controls": sum(x["sat_negatives"] for x in measured),
        "independent_concrete_cases_explicit": sum(
            x["independent_concrete_cases"] for x in measured
        ),
        "records": measured,
        "verdict": "ALL_ACCEPTED_AL03_REGRESSIONS_VERIFIED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--concrete", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = validate(
        json.loads(args.manifest.read_text()),
        args.source.read_text(),
        args.concrete.read_text(),
        args.proofs.read_text(),
        args.source_sha,
    )
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        "AL03_MANIFEST "
        f"suites={report['suites']} concrete={report['concrete_tests']} "
        f"z3={report['external_z3_tests']} "
        f"unsat={report['real_unsat_witnesses']} "
        f"sat={report['sat_negative_controls']} "
        f"explicit_concrete={report['independent_concrete_cases_explicit']} "
        "status=VERIFIED"
    )


if __name__ == "__main__":
    main()
