# FZ-3 native differential: persistent cross-run triage

The scheduled [FZ-3 native campaign](../.github/workflows/fz3-nightly-native-differential.yml)
runs **only on `compiler-ci`**. It compares TypeId native GAS/direct ELF
execution with the independent original-IR reference. Source SHA, UTC seed
window, per-run `fingerprints.tsv`, IR reproductions, and `replay.txt` remain
in Actions artifacts for 14 days.

## Persistent history

A dedicated **`fz3-native-fingerprint-history`** branch contains only
`state/fz3/fingerprints.tsv` and `state/fz3/processed_runs.tsv` (plus
its initial checkout files). History is kept separately from the
default branch to avoid a daily stream of unrelated CI-source commits.
The nightly job checks this branch out with repository-scoped
`GITHUB_TOKEN` credentials (`contents: write`). Its concurrency group
serializes scheduled and manual executions, preventing two runs from
silently replacing one another. A write failure fails the nightly job.

`tools/fz3_triage.py` validates the actual campaign `summary.txt`,
checks its **exact compiler SHA** and fingerprint counts, refuses injected
self-tests, detects duplicate or malformed rows and verifies that each
reported finding has original, compiled, minimized and replay files.
The history key is the exact tuple:

`(optimizer mode, native sink, i32 a, i32 b, reference u32, native u32)`

The stable fingerprint ID is a versioned SHA-256 digest truncated to 20
hex characters. It identifies a *wrong-value symptom*, not necessarily
a unique underlying compiler bug. Each history record tracks the first
and latest source SHA/run, number of observing runs and total observations.
The processed-run ledger includes the exact GitHub run/attempt token and
a digest of the source report, making retries idempotent and rejecting
silent replay with different data.

## New and known findings

Only a previously unseen genuine wrong-value key causes a **new issue
candidate**. The nightly workflow searches existing open/closed
`compiler-ci` issues for `FZ3-FP:<id>` and opens a new issue only if
none exists. The issue contains the exact fingerprint, triggering
operands and source revision, a link to the validating Actions run, and
instructions for retrieving the minimized IR and replay report.

Already-known fingerprints increase the persistent count but cause **no
duplicate issue**. The nightly job commits the ledger after successfully
processing new-issue candidates. If issue creation fails, history is not
published; retrying searches for existing issue IDs first.

A normal campaign that finds **zero mismatches** is still recorded in
`processed_runs.tsv` to distinguish a genuine clean run from missing
execution. Infrastructure errors, missing reports, invalid fingerprints,
or injected results are never inserted into genuine history. The
artifact upload uses `if: always()`, including triage output on failure.

## CI validation and recovery

The `tests/test_fz3_triage.py` suite covers new/known fingerprints,
cross-revision recurrence, retry idempotency, GAS/ELF distinction, malformed
paths and signatures, duplicate rows, and **injected self-test rejection**.
The dedicated `fz3-triage-validation.yml` workflow also runs an actual
bounded no-injection native campaign and reconciles its report twice.

To inspect history:

```sh
git fetch origin fz3-native-fingerprint-history
git show origin/fz3-native-fingerprint-history:state/fz3/fingerprints.tsv
git show origin/fz3-native-fingerprint-history:state/fz3/processed_runs.tsv
```

History/triage changes never claim a compiler bug is fixed. Human review
must reproduce the original exact signature, compare the minimized IR
and pipeline mode, assign a root cause, and link the relevant fix.
The nightly job still fails on *known* genuine wrong-value mismatches:
known findings are deduplicated for triage, **not hidden from CI status**.

The history is only a compact registry; the IR artifacts are subject to
GitHub's retention period and should be preserved in a durable bug report
if needed beyond 14 days. New and recurring fingerprints are not
automatically merged by possible root cause, and this framework does not
prove the 21 optimizer core stages correct.
