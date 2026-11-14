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

## Persistent source-pass activity trends

The native campaign now emits `pass_coverage.tsv` from the existing
production TypeId stage observer. It records the **actual number of complete
optimizer invocations per input module recipe** (`visits`) and boundaries
where the serialized verified IR changed (`edits`). Counts cover each of
the **21 core passes**, each optional retry pass, CTFE and native-integer
optimization, separately for Disabled, ScalarLeaves and CostAwareCfg modes.

Raw/unoptimized native executions never count as optimizer invocations.
The file always has **132 explicit mode/stage rows**, including zero visits
for modes not reached under a bounded campaign. A pass edit is not evidence
of compiler correctness; the previous **FZ-2.5 Partial** classification
for all 21 core passes is unaffected.

The `tools/fz3_pass_trends.py` reconciler requires an authentic
non-injected native campaign, an exactly matching source SHA and a
previously validated native report/run in `processed_runs.tsv`. It rejects
unknown stages, repeated stage keys, inconsistent counts and impossible
`edits > visits`. No incomplete or injected campaign can silently be
counted as certified.

The dedicated history branch stores:

- `state/fz3/pass_runs.tsv`: 132 per-run rows, with exact run attempt,
  source SHA, inline mode, stage, visits, edits and a budget-truncation flag
- `state/fz3/pass_totals.tsv`: cumulative run counts, visits, edits and
  counts of runs in which each stage was reached or changed
- per-run `pass_trends/pass_trend.md` and `pass_trend.tsv` artifacts,
  listing the most active edited stages and total history

Reconciliation is idempotent: repeating the same CI run must preserve its
counts, while replaying it with different data is rejected. The ledger
is published only after the normal FZ-3 native triage, archived-witness
checks and issue actions succeed. The raw artifact remains available on
both clean and failure runs.

These metrics track **observed pass activity**, not branch coverage,
opcode coverage, a correctness oracle for each pass, or performance.
More source-semantic test cases and independently checked pass interactions
are still required for certification.

## Archived-witness lifecycle and regression detection

A rotating fuzz campaign **cannot** prove that a bug is fixed merely because
its fingerprint is absent from a subsequent nightly run. The nightly workflow
now persists the **minimized `.ir` witness** for every new genuine fingerprint
under `state/fz3/witnesses/<fingerprint-id>.ir` on the same durable history
branch. Only validated, non-injected reports may populate these files.

On subsequent runs, `tools/fz3_recheck.py` builds the unmodified current
compiler's `typeid_native_diff_repro` executable and replays up to **16
existing witnesses**, with a per-witness timeout. The starting index rotates
by the full 16-witness window each run, so a history larger than the nightly
budget is sampled fairly rather than permanently starving newer findings. It compares actual native
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
