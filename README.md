# Compiler correctness validation runners

This public repository runs compiler correctness tests against the private
[`bingordo/compiler`](https://github.com/bingordo/compiler) source, using a read-only deploy key. It also verifies prebuilt expression-rule generator artifacts.

The source repository builds a standalone Linux x86-64 generator binary when
the generator changes, then stores an archive, checksum and generating commit
in its private `expr-rule-gen-binaries` branch.

[`test-expression-rule-generator.yml`](.github/workflows/test-expression-rule-generator.yml)
fetches only `dist/` from that branch using this repo's **SSH deploy key**
`CI_KEY`, validates its SHA-256, installs Z3 and runs executable-level tests:
proof-backed generation, deterministic regeneration, automatically inferred
guarded arithmetic/bitwise identities, and fail-closed proof budgets. It **never clones the compiler source** or invokes Cargo.

The workflow runs on updates to this test configuration or on manual dispatch.
To test a newly published binary, use **Actions → Verify prebuilt expression
rule generator → Run workflow**. There is deliberately no scheduled run.

The bounded conditional template search currently discovers **62 proven
rules** including `(x + A) - B => x` when `A == B`, and
`((x | A) & B) => (x & B)` when `(A & B) == 0`. Its tests reject the
corresponding unconditional transformations. The generator remains a
research experiment; none of these rules is enabled in production optimization.


## Existing compiler correctness checks — binary-only heavy test offload

The [migration runbook](docs/heavy-validation-migration.md) distinguishes
fast source-repository tests from the longer independent proof/GCC campaigns.

**Alive-lite source-build verification:** [verify-alive-lite.yml](.github/workflows/verify-alive-lite.yml) now checks out the exact compiler source using CI_KEY, compiles the independent compiler core in this repository's runner, installs Z3, and executes the ignored proof tests with nonzero coverage validation. It runs every 15 minutes or manually and caches successful results by source SHA and workflow hash. No source-repository Actions publisher is needed. A green end-to-end run is still required for acceptance.

**Mini-C source-build differential:** [verify-mini-c-differential.yml](.github/workflows/verify-mini-c-differential.yml) checks out the compiler with the existing read-only CI_KEY, builds its independent Mini-C driver workspace on compiler-ci runners (using the public Mini-C dependency), and executes fixed, seeded, mixed-ABI and negative GCC oracles. It runs every 15 minutes or manually; exact source SHA, workflow version and seed count cache successful results. This no longer depends on publishing a binary through exhausted compiler-repository Actions minutes. Builds and tests have separate timeouts and preserve failure logs.

The Alive-lite job additionally runs `tools/validate_alive_lite_evidence.py` on all four emitted JSONL artifacts, requiring complete unique pass/fixture matrices, real changed transformations, exact proof source SHAs, and strict separation between `BOUNDED_ONLY`, `NOT_EXERCISED`, and `PROVEN`. Twelve Python tests (eleven negative controls and one valid fixture) exercise that accounting gate; it does not replace the actual Z3/native tests. This extra check is part of the existing job, not a second expensive campaign.

**Validation status:** Mini-C and Alive-lite source-build workflows have been reported green. Both poll for source changes every 15 minutes, skipping exact revisions already validated by the corresponding workflow version. The older source-side binary publishers are no longer prerequisites for these two gates. Check individual Actions runs for exact source SHA and test counts.

## M10 Csmith coverage and harness correctness (manual native campaign)

The compiler's [M10 Csmith plan](https://github.com/bingordo/compiler/blob/main/docs/Csmith_Validation.md) tracks unmodified Csmith-generated translation units. The [manual coverage workflow](.github/workflows/verify-csmith-coverage.yml) downloads a SHA-256-verified, source-commit-pinned **compiled Mini-C driver binary**; it does not rebuild private compiler sources here. It records original source hashes, standard C preprocessing, agreeing GCC/Clang reference outputs, first unsupported frontend diagnostics and native outcomes separately. Its options include the reviewed `restricted-observable` and `restricted-compound` profiles. A source-push event runs only lightweight harness regressions; the expensive Csmith corpus runs **only** on manual dispatch.

The current measured [eight-seed run #41851768161](https://github.com/bingordo/compiler-ci/actions/runs/41851768161) had **8/8 agreeing reference executions and 0/8 native Mini-C executions**, blocked on hosted floating-point declarations. This is frontend coverage measurement, **not a compiler correctness pass**. See [M10 issue #316](https://github.com/bingordo/compiler/issues/316) for exit criteria before increasing the corpus.

The [small source-only harness workflow](.github/workflows/verify-csmith-harness-source.yml) can be manually given an exact compiler source branch/SHA; a change to its own workflow tests compiler `main`. It checks out only the Python harness and pinned Mini-C manifest over the existing read-only `CI_KEY`, compiles both Python files and runs the fail-closed regression suite. [Run #41861539819](https://github.com/bingordo/compiler-ci/actions/runs/41861539819) passed **32 tests** on compiler commit `bea3b5b42e29b6d3cafd4438a5e34ff75a99feeb`, including campaign deadline accounting. No Csmith generator or compiler code build is used in this short gate.

A manual native probe has a **360-second soft budget**, evaluated before starting each seed, followed by the existing 8-minute outer hard timeout. On soft-budget exhaustion, each unstarted seed gets an immutable source hash and replay entry with `budget-exhausted`; the campaign returns a nonzero result and `INCOMPLETE_BUDGET` rather than reporting partial success. An already running seed may finish beyond the soft budget, subject to its individual subprocess limits and the hard workflow timeout. Evidence is uploaded even if the probe fails. Native acceptance remains zero until unmodified Csmith programs compile, link, execute and match both references.


## EDG/Cranelift YARPGen differential campaigns

[edg-yarpgen.yml](.github/workflows/edg-yarpgen.yml) owns the expensive
EDG/Cranelift YARPGen runs formerly embedded in `bingordo/edgcpp-rust`.
Use **Actions → EDG Cranelift pinned YARPGen differential → Run workflow**,
select `source_ref` (defaults to `cranelift-codegen`), `count` (10 or 100),
and `start_seed`. It downloads the pinned EDG and YARPGen releases, builds
only the Rust Cranelift driver once with Cargo caching, then compares generated
programs against GCC and Clang. It does not rebuild the EDG frontend library.

The result artifact contains provenance, per-seed classification and failure
reproducers. Unsupported cases are **not** counted as passing. The workflow automatically checks the `cranelift-codegen` branch every 15 minutes. A lightweight preflight compares the current source commit against a completed-cohort cache marker, so unchanged commits skip the expensive job. New commits run a 10-seed differential cohort; 100 seeds remain manual. Changes to the workflow also trigger a run. This is polling, not a cross-repository webhook, and GitHub schedules may be delayed. The cache marker is written only after a successful scheduled campaign; failed campaigns are retried. Routine targeted native
regressions stay in `edgcpp-rust`.


## Native CFG correctness offload

[`verify-compiler-core-correctness.yml`](.github/workflows/verify-compiler-core-correctness.yml)
validates native-code correctness on the private `bingordo/compiler` source using the existing read-only `CI_KEY` deploy key
without consuming Actions minutes from `compiler`. It runs the permanent
`native_cfg_differential`, `loop_trap_semantics`, `memory_execution_oracle`,
`phi_native`, and backend `division_native` Rust integration tests, plus the
`verifier_mutations` IR suite and the `alias_phi_exhaustive`,
`pre_licm_semantic_matrix`, `induction_native`, `pre_native`, and
`mem2reg_native` optimization suites.

The expanded gate also tests thousands of invalid SSA mutations, nested and
multi-latch loops, PRE/LICM interactions, conservative alias reasoning,
and induction safety. Existing tests exercise x86 GNU assembly and direct ELF against an
independent C oracle, conditional SIGFPE traps, signed integer edge cases,
memory ordering and pointer PHIs. This is correctness only; **there are no
timing benchmarks or performance thresholds**.

The workflow checks `compiler`'s `main` every 15 minutes, but only runs
Cargo if the exact source SHA has not previously passed this workflow
version. Manual dispatch can select a different branch/SHA or force a
recheck. A changed workflow is also verified before merging. Cached core
Rust artifacts are reused, and test logs plus native reproducers are saved
on failure. This builds only the dependency-free IR/optimizer/backend crate
closure from a temporary checkout: it does not need or fetch the private
Oberon frontend or a compiler-source Actions workflow. The source itself is
private, so checkout must use `CI_KEY`, not the default `GITHUB_TOKEN`.

After changes to these tests, inspect the first **real** green Actions run
and the complete source commit SHA. A successful `compiler` PR merge alone
is not evidence that the external suite ran.

**Scope and scheduling:** The full 11-target correctness gate is automatically
checked on an exact compiler source revision by the existing 15-minute
`compiler-ci` poll. A previously passing source SHA + workflow version
skips Cargo entirely. Both the core native and expanded IR/optimizer
targets must pass before evidence is cached. Required test-name checks
protect against zero-test false greens, and all failure logs are uploaded.
This is intentionally **not** a general benchmark or full-workspace build:
new compiler features need targeted regression additions, while
solver-heavy Alive-lite proofs and Csmith remain separate workflows.
