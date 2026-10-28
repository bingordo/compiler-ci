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
