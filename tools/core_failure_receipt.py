"""Source-free receipts for completed ordinary core test failures."""
import json
import os
import sys
from pathlib import Path


def classify(report, source_sha, run_url):
    if not isinstance(report, dict) or report.get("source_sha") != source_sha:
        return None
    rows = {row["suite"]: row for row in report.get("suites", [])}
    build, tests = rows.get("cargo-build", {}), rows.get("cargo-tests", {})
    counts = tests.get("test_counts") or {}
    if not (build.get("status") == "passed"
            and tests.get("status") == "failed"
            and tests.get("exit_code") == 101
            and not tests.get("rustc_error_codes")
            and tests.get("failed_test_case_ids")
            and all(type(counts.get(k)) is int and counts[k] >= 0
                    for k in ("passed", "failed", "ignored"))
            and counts["passed"] > 0 and counts["failed"] > 0):
        return None
    return {"source_sha": source_sha, "run_url": run_url,
            **{k: counts[k] for k in ("passed", "failed", "ignored")}}


def main():
    source, target = map(Path, sys.argv[1:])
    if not source.is_file():
        return
    report = json.loads(source.read_text())
    url = (os.environ["GITHUB_SERVER_URL"] + "/" + os.environ["GITHUB_REPOSITORY"]
           + "/actions/runs/" + os.environ["GITHUB_RUN_ID"])
    receipt = classify(report, os.environ["SOURCE_SHA"], url)
    if receipt is None:
        return
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(receipt))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        print("reusable=true", file=output)


if __name__ == "__main__":
    main()
