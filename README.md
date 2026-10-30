# Offline expression rule generator verification

This public repository contains only the **test workflow** for the
prebuilt `expr-rule-gen` executable; implementation stays in the private
[`bingordo/compiler`](https://github.com/bingordo/compiler) repository.

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

**Alive-lite proof consumer:** [verify-alive-lite.yml](.github/workflows/verify-alive-lite.yml)
fetches the actual compiled SSA/CFG optimizer test executable from
`expr-rule-gen-binaries/dist/alive-lite/` using `CI_KEY`, verifies the binary
SHA-256 and source commit, and runs bounded, **nonempty** Z3 tests. Successful
proof results are cached by exact binary checksum, and `force_recheck` can
bypass the cache for manual regression confirmation.

**Alive-lite source-build verification:** [verify-alive-lite.yml](.github/workflows/verify-alive-lite.yml) now checks out the exact compiler source using CI_KEY, compiles the independent compiler core in this repository's runner, installs Z3, and executes the ignored proof tests with nonzero coverage validation. It runs every six hours or manually and caches successful results by source SHA and workflow hash. No source-repository Actions publisher is needed. A green end-to-end run is still required for acceptance.

**Mini-C source-build differential:** [verify-mini-c-differential.yml](.github/workflows/verify-mini-c-differential.yml) checks out the compiler with the existing read-only CI_KEY, builds its independent Mini-C driver workspace on compiler-ci runners (using the public Mini-C dependency), and executes fixed, seeded, mixed-ABI and negative GCC oracles. It runs every six hours or manually; exact source SHA, workflow version and seed count cache successful results. This no longer depends on publishing a binary through exhausted compiler-repository Actions minutes. Builds and tests have separate timeouts and preserve failure logs.

**Live status:** The Alive-lite binary publisher/consumer handoff remains unverified. Mini-C now builds directly here, and its first successful end-to-end source-build differential run remains to be confirmed. On October 30, 2026, the source-side Alive-lite
publisher [failed before any job step executed](https://github.com/bingordo/compiler/actions/runs/41010696186),
while unrelated source workflows showed the same pre-step failure.
Do not claim either test gate passes until a real successful run has been inspected.

## Future Csmith whole-program compiler tests (planned; not enabled)

The compiler's [M10 Csmith validation plan](https://github.com/bingordo/compiler/blob/main/docs/Csmith_Validation.md)
reserves **this repository** for larger, slower compiler differential campaigns.
It is a *future goal*; no Csmith workflow, accepted Csmith corpus or passing
Csmith correctness gate exists here yet. Csmith normally generates programs
using language/runtime features that the current Mini-C compiler does not
fully implement.

When the source compiler can compile unmodified Csmith translation units,
the source repository will publish **only a pinned compiled compiler/driver
binary**, with its source commit and SHA-256 checksum, to the private
distribution branch. This repository's already configured read-only
`CI_KEY` will fetch **only the binary artifacts**—not private compiler
source, not Cargo dependencies, not any frontend deploy keys.

Planned progression: start with manually triggered **8–16 fixed seeds** and
GCC+Clang-agreed native output; after real acceptance, add **64-seed
manual milestone tests**, with bounded 20–30 minute jobs, a single active
campaign and a verified seed replay. Introduce weekly campaigns only if
the manually run corpus produces useful validation. Cache one pinned Csmith
generator/runtime installation and precomputed reference outputs rather than
rebuilding either compiler or generator per seed. Enforce per-compiler and
per-executable timeouts; record unsupported C, wrong-code, crashes,
timeouts, and reference disagreement separately. Report acceptance ratios
and successful comparisons, **not** a misleading green result when all
generated programs are unsupported.

Keep everyday compiler PR validation targeted and short. Full Csmith
campaigns belong here rather than on each `bingordo/compiler` push.


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
