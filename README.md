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
reproducers. Unsupported cases are **not** counted as passing. The workflow
is manual-only until a tested binary publication/trigger path exists; there
is no automatic cross-repository dispatch yet. Routine targeted native
regressions stay in `edgcpp-rust`.
