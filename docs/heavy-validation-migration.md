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

1. In `bingordo/compiler`, dispatch `publish-alive-lite.yml` from `main`. This compiles the test harness but does **not** run Z3.
2. Confirm the publisher succeeded and `expr-rule-gen-binaries:dist/alive-lite/` contains `alive-lite-linux-x86_64.tar.gz`, `sha256.txt` and `source-sha.txt`.
3. In `bingordo/compiler-ci`, dispatch `verify-alive-lite.yml`. Set `expected_source_sha` to the exact source SHA published in step 2; this prevents validating a stale artifact.
4. Confirm the Z3 proof job passes and inspect its logs. On failure, download any available reproducer artifact.
5. Only after a successful end-to-end run, consider deleting the remaining manual Alive-lite step in `compiler/core.yml` and adding an appropriately bounded schedule here.

**Status as of 2026-10-29:** publisher and consumer workflow files merged; no successful publication or proof execution has been verified. Do not count the offload as validated yet.
