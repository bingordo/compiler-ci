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

**Mini-C source-build differential:** [verify-mini-c-differential.yml](.github/workflows/verify-mini-c-differential.yml) checks out **current `bingordo/compiler/main` once daily at 09:23 UTC**, builds the standalone Mini-C driver with the existing read-only `CI_KEY`, and runs fixed, seeded, mixed-object ABI, and negative GCC oracles. The same daily job reuses existing native Mini-C/GCC tests: mixed GP/XMM floating-point ABI and scalar FP ELF/GAS checks, the indirect GP variadic `printf` source oracle (ELF/GAS, with/without frame pointer), and the full mixed ABI matrix under explicit frame pointers. Rust compilation caches dependencies and objects, but completed-source results are **not** cached/skipped: a daily invocation genuinely tests main. Manual dispatch remains available for an explicit source SHA. The `mini-c/main-daily` commit status links to the actual run, and the job summary records the checked-out compiler SHA. No compiler-repo Actions minutes or candidate-specific ABI workflow are needed.

**Upstream GCC binary compatibility pilot:** The same daily main gate runs [`tools/gcc_compat_abi_pilot.py`](tools/gcc_compat_abi_pilot.py), covering four original GCC `gcc.dg/compat` triads: `scalar-by-value-2`, `scalar-return-1`, `sdata-1`, and `pr83487-1`. It downloads unmodified upstream sources and headers at immutable GCC revision `1330eed38849ad186518f609c52bd5651c2bfdc1`, verifies Git blob hashes, proves a GCC/GCC reference, then replaces each original `main.c`, `_x.c`, or `_y.c` object with a Mini-C-built object independently. Rejections are **missing frontend coverage**, never ABI passes; successful objects must link and execute identically to GCC/GCC, and observed ABI mismatches fail the gate.

[Native compiler-ci run #45549326184](https://github.com/bingordo/compiler-ci/actions/runs/45549326184) passed all existing ABI oracles and admitted **6/12** original GCC mixed directions on compiler `2361d2cb2e7ef69b298f0f81d2a26f22d360cf37`. The four mandatory directions are original `scalar-by-value-2_main.c`, `scalar-return-1_main.c`, and both **`sdata-1_x.c` and `sdata-1_y.c`**. The `sdata-1` case exercises real cross-object struct storage, field/array addressing, external symbol references, and GCC-linked execution, not just a trivial main function. Six other directions remain explicit `COMPILE_REJECTED` coverage gaps (FP scalar forms, variadic/preprocessed declarations, and GNU empty/overaligned aggregate arguments). This is **not** full C SysV ABI coverage; see [compiler issue #3108](https://github.com/bingordo/compiler/issues/3108). GCC test sources stay upstream under their GNU GPL licensing; only immutable hashes and an adapter are stored here.

The Alive-lite job additionally runs `tools/validate_alive_lite_evidence.py` on all four emitted JSONL artifacts, requiring complete unique pass/fixture matrices, real changed transformations, exact proof source SHAs, and strict separation between `BOUNDED_ONLY`, `NOT_EXERCISED`, and `PROVEN`. Twelve Python tests (eleven negative controls and one valid fixture) exercise that accounting gate; it does not replace the actual Z3/native tests. This extra check is part of the existing job, not a second expensive campaign.

**Validation status:** Mini-C runs against current compiler main daily, without a success-cache skip. Alive-lite has its own schedule and revision-cache policy. The older compiler-source binary publishers are not prerequisites. Check the `mini-c/main-daily` status and the linked Actions run for the exact tested compiler SHA and test results; a merged workflow is not evidence the next scheduled run has passed.

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

## Historical workflow archive and schedule hygiene

Older exact-source milestone workflows that only reran when their own YAML
changed have been moved to [the historical workflow archive](ci/archive/README.md).
The original workflow bytes and compiler pins are preserved for recovery, but
the archived jobs are no longer in GitHub Actions' active workflow directory.
Current-source compiler validation (including Mini-C, compiler-core, backend,
proof and fuzz checks) stays active. Fixed-commit historical checks can be
replayed with `workflow_dispatch`, but do not poll unchanged compiler source.
The lightweight [workflow hygiene check](.github/workflows/workflow-hygiene-inventory.yml)
rejects newly scheduled source-pinned workflows and checks archive isolation.

### Retired legacy 15-minute polling

The three legacy workflows below remain in `.github/workflows/` for exact
manual replay (and any self-file change trigger), but no longer consume
scheduled runners every 15 minutes:

- `verify-x86-isl.yml` validates the historical `feature/x86-cost-inventory`
  branch; current-main x86 checks are maintained separately.
- `verify-x86-t5-bridge.yml` compares an experimental generator branch with
  current compiler main. Run manually when explicitly testing that bridge.
- `verify-driver-native.yml` verifies a published ABI test archive in
  `expr-rule-gen-binaries`. Run manually after a new publication. Its
  SHA/publication validation and cached-pass gates remain unchanged.

Together these changes eliminate **288 polling invocations/day** (2,016/week)
without removing any test definitions. They do not replace or disable
current-compiler main source checks, differential fuzzing or proof suites.
