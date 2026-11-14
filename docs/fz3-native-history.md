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

A rerun is tracked by `run_id.run_attempt`; it is a separate validated
attempt even when GitHub reuses the same Actions run URL. The ledger is
updated only after issue triage succeeds. A jobless GitHub workflow dispatch
or missing report is **not** recorded as a clean campaign.

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

## Archived-witness lifecycle and regression detection

A rotating fuzz campaign **cannot** prove that a bug is fixed merely because
its fingerprint is absent from a subsequent nightly run. The nightly workflow
now persists the **minimized `.ir` witness** for every new genuine fingerprint
under `state/fz3/witnesses/<fingerprint-id>.ir` on the same durable history
branch. Only validated, non-injected reports may populate these files.

On subsequent runs, `tools/fz3_recheck.py` builds the unmodified current
compiler's `typeid_native_diff_repro` executable and replays up to **16
existing witnesses**, with a per-witness timeout. It compares actual native
GAS or ELF execution against the independent reference defined by the *saved
original IR*, for the precise operand pair, optimization policy and sink.

`state/fz3/rechecks.tsv` stores the last checked full compiler SHA and run,
current exact signature state, the first agreeing SHA and the number of
**distinct compiler revisions** that successfully replay with native/reference
agreement. A run may report:

- `reproduces` — the original exact wrong-value result persists
- `not-reproduced` — executed saved IR now agrees with its reference; this
  is a **possible fix**, not a correctness proof
- `other-wrong-value` — output changed but is still wrong; not fixed
- `unavailable` — original IR cannot be replayed, compiler/oracle fails,
  expected reference changes or a timeout occurs; never considered fixed

A first agreeing replay posts a tentative-fix comment on the existing
fingerprint issue, **without closing it**. A second agreeing replay on a
different full compiler SHA posts an additional evidence comment. If the
*exact* wrong value reappears after previous clean reproduction, the workflow
posts a **possible regression** and reopens the issue if it was closed.
Changed wrong values receive their own note without claiming a fix.
No duplicate issue is created. The lifecycle `events.tsv` is uploaded
along with the full native report and minimized IR artifacts.

Issue comments and reopening are only attempted for real, validated
transitions. They must succeed **before** the updated persistent history can
be committed; on failure the job and published state remain unchanged.
Scripted tests simulate all transitions, invalid snapshots, missing
witnesses, timeouts and reference changes. The nightly job remains red
for genuine detected wrong-code, regardless of its classification as known.

This is conservative *per-witness* evidence. It does not prove a root-cause
fix, does not automatically close issues, and does not retroactively mark
unseen seed combinations as passing. A `not-reproduced` recheck on one
minimized input is not a substitute for the full native differential suite.

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
