# Expensive compiler validation migration

## Goal
Keep `bingordo/compiler` PR checks fast. Run expensive validation here against immutable compiler commits, with bounded runner time and reproducible results.

## Existing infrastructure
`CI_KEY` is a read-only SSH deploy key for the private `expr-rule-gen-binaries` distribution branch. The expression generator test already downloads a checksum-verified executable and does not compile compiler sources.

## Migration order
1. **Alive-lite Z3 proofs:** source-level `cargo test -p compiler-opt --test alive_lite -- --ignored` cannot currently be run from the published expression-generator executable. Publish a dedicated standalone proof-test binary (or a verified source/test bundle), with checksum, source SHA and architecture. Do not claim the existing generator binary covers these proofs. Until available, retain manual `workflow_dispatch` on the source repository.
2. **Full integration / differential tests:** publish a compiler driver binary with a SHA-256 checksum and exact source SHA. Download and test it here, not by rebuilding compiler per seed.
3. **Csmith:** follow the existing bounded manual-first plan, distinguish unsupported programs from successful comparisons, and promote to weekly only after acceptance is demonstrated.
4. **Cross-target / AArch64:** use native ARM runners for runtime tests where available; retain cheap cross-compile checks in the source repository.

## Runner-time discipline
- Default to manual dispatch; add scheduled campaigns only after useful signal is established.
- Set job timeouts, concurrency cancellation and bounded seed counts.
- Reuse binaries, fixtures and dependency caches.
- Run fast tests in `compiler` and slow tests here; avoid duplicate triggers.
- Publish commit SHA, artifact checksum, test counts, wall time, failure reproducers and job duration.
- Keep source-level proofs available until replacement coverage has been verified.
- Do not use the private source repository's Oberon deploy key in this repository.

## Measurement
Compare per-job and per-step durations, cache restore/save timings and cancellation frequency against the October 24–29 billing baseline. Billing runner minutes are not CPU utilization. Prioritize `compiler/core.yml`, `edgcpp-rust/build.yml`, `pmu-tools-tests/audit-tests.yml`, then branch cleanup. Measure before changing test scope.

## Alive-lite operational runbook

1. After a relevant optimizer/IR main-branch change, the source workflow builds automatically. Otherwise dispatch `publish-alive-lite.yml` manually from `main`. This compiles the test harness but does **not** run Z3.
2. Confirm the publisher succeeded and `expr-rule-gen-binaries:dist/alive-lite/` contains `alive-lite-linux-x86_64.tar.gz`, `sha256.txt` and `source-sha.txt`.
3. In `bingordo/compiler-ci`, dispatch `verify-alive-lite.yml`. Set `expected_source_sha` to the exact source SHA published in step 2; this prevents validating a stale artifact.
4. Confirm the Z3 proof job executes at least one ignored test (not just a zero-test green outcome), passes, and inspect its logs. On failure, download any available reproducer artifact. Use `force_recheck=true` for intentional repeated proof execution.
5. Only after a successful end-to-end run, consider deleting the remaining manual Alive-lite step in `compiler/core.yml` and adding an appropriately bounded schedule here.

## Mini-C GCC differential operational runbook

1. The source publisher [publish-mini-c-differential.yml](https://github.com/bingordo/compiler/blob/main/.github/workflows/publish-mini-c-differential.yml)
   builds a single pinned Mini-C driver binary after changes to the Mini-C bridge
   or test harness, or on explicit milestone dispatch. Backend/IR-wide milestones
   should dispatch explicitly; do not automatically rebuild the private frontend
   on every compiler-core commit. This requires only the existing Mini-C read-only
   deploy key on the **source** side.
2. Confirm the source publisher succeeded and that
   `expr-rule-gen-binaries:dist/mini-c-differential/` contains the archive,
   `source-sha.txt` and `sha256.txt`.
3. In this repository dispatch [verify-mini-c-differential.yml](../.github/workflows/verify-mini-c-differential.yml)
   with the **exact source SHA**, plus `seed_count=6` for a bounded first run.
   Set `force_recheck=true` for deliberate repeat tests.
4. The consumer verifies checksums and the exact source SHA, reuses the **binary,
   not compiler Cargo sources**, then runs the bundled version of the GCC
   differential harness with a 16-minute hard ceiling, 15-second per-process
   budget and 1–64 seeded cases, *plus all fixed cases, mixed ABI and negative
   tests*. It demands a nonempty success report and uploads any failing
   reproducers. Successful results are cached by binary checksum and seed count.
5. Only after a real publisher→consumer success should the team consider
   recurring larger seed sets or a change-triggered cross-repository dispatch.
   The existing `CI_KEY` is **read only**; it does not authorize pushing a
   dispatch into this repository.

## Status and remaining verification (2026-10-30)

- **Implemented:** Source [Alive-lite publisher PR #134](https://github.com/bingordo/compiler/pull/134)
  automatically packages the *actual compiled test executable* when the owning
  IR/optimizer/test code changes. The [external proof consumer](../.github/workflows/verify-alive-lite.yml)
  caches a validated exact SHA-256 binary, bounds Z3 execution to 12 minutes,
  refuses a green run with zero executed ignored proof tests, and retains
  reproducers on failure. An explicit `force_recheck=true` bypasses the cache.
- **Implemented:** Mini-C driver bundle publisher filters normal pushes to
  bridge/harness edits, supports manual broader milestones, reuses Rust caches,
  and safely retries distribution-branch updates. The matching external consumer
  keeps the long fixed+seeded GCC campaign here rather than compiling the
  frontend again in this repository.
- **NOT YET VERIFIED:** The [first automatic Alive-lite publisher run](https://github.com/bingordo/compiler/actions/runs/41010696186)
  failed before any step started. Other unrelated `compiler` workflows failed
  before running steps at the same time. The Actions job metadata contains zero
  steps, and its log blob was unavailable, so there is **no evidence of a Rust
  build or proof failure**. The `dist/alive-lite/` and
  `dist/mini-c-differential/` artifacts were still absent when last checked.
  Neither source-to-external offload is a passed correctness gate yet.
- **Next operational gate:** when source GitHub Actions can start jobs again,
  validate one successful publish in each workflow, dispatch the matching
  `compiler-ci` consumer with the **published** source SHA and review
  full native/proof logs and failure artifacts. Do not delete existing source
  tests, enable expensive schedules, or report coverage as passed until then.

This blockage concerns the **heavy-test handoff** only; existing targeted
compiler-opt correctness tests and other independently verified
`compiler-ci` native oracles remain distinct results. Csmith/M10 stays deferred.
