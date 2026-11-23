# Historical source-pinned compiler-ci workflows

Archived on 2026-11-22 in the `ci/workflow-hygiene-20261122` cleanup.

These 117 workflows checked out exact historical compiler commits and had **no schedule or live compiler-source-change trigger**. Their automatic push/PR triggers, where present, only watched the workflow's own YAML. They remain in Git history and here as byte-for-byte archived blobs, but cannot be manually dispatched through GitHub Actions until restored to `.github/workflows/`.

Archived workflows are historical experimental/evidence runs, **not current-main regression gates**. This is a conservative first batch: recent X7/RISC-V, the 221 `verify-*` candidates and active scheduled dynamic-source gates were intentionally retained. The other two obsolete, fixed-commit schedules are converted to manual-only and remain active.

To replay: copy the relevant YAML back to `.github/workflows/` in a feature branch or use the preserved source SHA and exact steps to build a new source-pinned one-off CI workflow. Never change a historical pin silently. Do not substitute archived tests for current-main validation.

## Original workflows and their compiler pins

| Original path | Original pinned SHA prefix |
|---|---|
| `.github/workflows/ct03-licm-source-abba.yml` | `6736f138e204` |
| `.github/workflows/ct03-phi-source-abba.yml` | `4ad9dec1ad9b` |
| `.github/workflows/ct03-scalar-sccp-source-abba.yml` | `6dbbe8b86b94` |
| `.github/workflows/ct05-identical-branch-abba.yml` | `682fb4f882d1` |
| `.github/workflows/ct05c-budget-abba.yml` | `8e0c6610d556` |
| `.github/workflows/ct05d-select-budget-abba.yml` | `1519841b2a20` |
| `.github/workflows/ct05e-direct-compare-abba.yml` | `5c9cb8a2cad8` |
| `.github/workflows/ct10-native-typeid-validation-profile.yml` | `e0b5f0a2a52d` |
| `.github/workflows/ct11-checked-loop-source-profile.yml` | `72c6d1bf41db` |
| `.github/workflows/ct11-typed-core-pass-profile.yml` | `c35eddda10da` |
| `.github/workflows/ct11-typed-licm-phase-profile.yml` | `dc2503e124a9` |
| `.github/workflows/ct11-typed-mem2reg-phase-profile.yml` | `45b9e62d48fb` |
| `.github/workflows/ct11-verified-licm-handoff-abba.yml` | `72c6d1bf41db` |
| `.github/workflows/docs-pr1522-validation.yml` | `0b421698e217` |
| `.github/workflows/docs-pr1524-validation.yml` | `8248837c9e38` |
| `.github/workflows/docs-pr1528-validation.yml` | `5a93d323dea8` |
| `.github/workflows/docs-pr1559-validation.yml` | `f9269c5ba6f5` |
| `.github/workflows/docs-pr1565-validation.yml` | `2880ba0c6b5d` |
| `.github/workflows/docs-pr1568-validation.yml` | `eb22c3b43c72` |
| `.github/workflows/docs-pr1605-validation.yml` | `3417228318c0` |
| `.github/workflows/docs-pr1609-validation.yml` | `010e805b673a` |
| `.github/workflows/docs-pr1653-validation.yml` | `c54700786d38` |
| `.github/workflows/expr-add-chain-cfg-diagnostic.yml` | `2ed3ee8400ee` |
| `.github/workflows/expr-add-chain-overhead-s21.yml` | `5612f82d02fd` |
| `.github/workflows/expr-add-chain-s21.yml` | `bd22beea3e9a` |
| `.github/workflows/expr-constant-folding-s16.yml` | `17e71bac2e0a` |
| `.github/workflows/expr-external-coverage-s15.yml` | `6ea7918a8fd0` |
| `.github/workflows/expr-external-source-s21.yml` | `1235d68d9f87` |
| `.github/workflows/expr-guard-basis-s14.yml` | `2f35510fcd14` |
| `.github/workflows/expr-guard-cegis.yml` | `7fcae79caaa1` |
| `.github/workflows/expr-guard-disjunctions.yml` | `794c9e8db3f0` |
| `.github/workflows/expr-opportunity-s21.yml` | `df16d6d974dd` |
| `.github/workflows/expr-real-source-expansion-s21.yml` | `8da617329d09` |
| `.github/workflows/expr-real-source-s17.yml` | `5e58ed6d23da` |
| `.github/workflows/expr-real-ssa-s18.yml` | `783571c743db` |
| `.github/workflows/expr-s14-real-ssa-s19.yml` | `c1264094aedb` |
| `.github/workflows/expr-s20-matcher-scale.yml` | `e8a105d958c8` |
| `.github/workflows/expr-s20-ssa-adapter.yml` | `e210d1b7beae` |
| `.github/workflows/expr-synthesis-proof-gates.yml` | `81cab4bd9dbe` |
| `.github/workflows/s21-des-crc-real-source-census.yml` | `723691fd192a` |
| `.github/workflows/s21-freebsd-numeric-codec-ssa.yml` | `2af25d9172cd` |
| `.github/workflows/s21-hash-crc-real-source-census.yml` | `8d6fed3c3127` |
| `.github/workflows/s21-prng-real-source-census.yml` | `8d6fed3c3127` |
| `.github/workflows/s21-real-bitnot-fold.yml` | `b880d9c14733` |
| `.github/workflows/s21-typed-algebra-cfg.yml` | `24306d3336e8` |
| `.github/workflows/s22-native-integer-generated.yml` | `4aded08b02a6` |
| `.github/workflows/s23-10-matchpd-partition.yml` | `41bcdc6944c8` |
| `.github/workflows/s23-11-matchpd-boolean.yml` | `c218d901d9a0` |
| `.github/workflows/s23-12-matchpd-complement.yml` | `d3489c52ded6` |
| `.github/workflows/s23-13-dag-index.yml` | `208467c1dc20` |
| `.github/workflows/s23-14-matchpd-factor.yml` | `73f04f1c3115` |
| `.github/workflows/s23-15-musl-typeid-source.yml` | `f021254e3849` |
| `.github/workflows/s23-16-native-phase-profile.yml` | `5e52f0fe9527` |
| `.github/workflows/s23-17-complement-xnor.yml` | `593077dd33c5` |
| `.github/workflows/s23-18-boolean-subset.yml` | `2575aa7ba9cd` |
| `.github/workflows/s23-19-pairwise-bitwise.yml` | `38a8acc0a6ee` |
| `.github/workflows/s23-20-wrapping-arithmetic.yml` | `72e547a7a76d` |
| `.github/workflows/s23-21-bitwise-comparison.yml` | `a1779b799092` |
| `.github/workflows/s23-22-subtractive-partition.yml` | `f4dbf29ee3e2` |
| `.github/workflows/s23-23-complement-disjoint-arithmetic.yml` | `62cf6934e1aa` |
| `.github/workflows/s23-24-xor-partitions.yml` | `5d1d56bc0fcd` |
| `.github/workflows/s23-25-complement-pair.yml` | `486fa552edb5` |
| `.github/workflows/s23-26-complement-mask.yml` | `f81006c941b9` |
| `.github/workflows/s23-27-carry-reconstruction.yml` | `9d2a16424b4c` |
| `.github/workflows/s23-28-add-partition-comparison.yml` | `ed536dd9a809` |
| `.github/workflows/s23-29-reverse-subtractive.yml` | `137a1580de19` |
| `.github/workflows/s23-30-reverse-carry.yml` | `5e093392068f` |
| `.github/workflows/s23-31-add-xor-carry-comparison.yml` | `760081ccbb16` |
| `.github/workflows/s23-32-shared-masked-sub.yml` | `1aba650e8c22` |
| `.github/workflows/s23-33-dual-arithmetic-cancellation.yml` | `863cb65765e5` |
| `.github/workflows/s23-34-additive-pair-cancellation.yml` | `3ec8ae0f515d` |
| `.github/workflows/s23-35-reverse-difference.yml` | `f491b87fa6ee` |
| `.github/workflows/s23-36-paired-complement.yml` | `c68155ea2f0c` |
| `.github/workflows/s23-37-demorgan-existing.yml` | `b1392507031a` |
| `.github/workflows/s23-38-native-source-cost.yml` | `06f3ca080402` |
| `.github/workflows/s23-39-whole-musl-tu-census.yml` | `7ef8fc8f763f` |
| `.github/workflows/s23-4-compare-dag.yml` | `fcfc6df1defc` |
| `.github/workflows/s23-40-musl-header-environment.yml` | `7ef8fc8f763f` |
| `.github/workflows/s23-42-complement-xor-pair.yml` | `e9ee2f8ae85d` |
| `.github/workflows/s23-43-unsigned-bitwise-subset.yml` | `42a0f388ebfd` |
| `.github/workflows/s23-44-strict-bitwise-partitions.yml` | `14dd03aa1221` |
| `.github/workflows/s23-45-comparison-prior-index.yml` | `2fdc0e86dcf6` |
| `.github/workflows/s23-46-matchpd-hit-census.yml` | `4898e5bf2955` |
| `.github/workflows/s23-47-typeid-native-snapshot-audit.yml` | `fd11745800fc` |
| `.github/workflows/s23-48-native-matcher-core-stage.yml` | `e333f9e6973b` |
| `.github/workflows/s23-49-independent-typeid-shapes.yml` | `b6822d162394` |
| `.github/workflows/s23-49b-external-source-demand.yml` | `fd8dcc2ae980` |
| `.github/workflows/s23-5-sign-order.yml` | `36604b562047` |
| `.github/workflows/s23-50-generated-typeid-shadow.yml` | `c6f5f218e983` |
| `.github/workflows/s23-50aa-generated-nearmisses.yml` | `87ed5e13deed` |
| `.github/workflows/s23-50ab-adversarial-fanout.yml` | `9995839354ca` |
| `.github/workflows/s23-50f-wrapping-cancellation.yml` | `ddf94562dd69` |
| `.github/workflows/s23-50g-boolean-producer-alias.yml` | `818abd849902` |
| `.github/workflows/s23-50h-gcc-boolean-admission.yml` | `00e1fa468a1c` |
| `.github/workflows/s23-50i-unary-complement-admission.yml` | `3c3e7c035603` |
| `.github/workflows/s23-50j-constructed-binary-admission.yml` | `ced83ab7bcf3` |
| `.github/workflows/s23-50k-two-op-factoring-admission.yml` | `8422fb300c37` |
| `.github/workflows/s23-50l-default-generated-optout.yml` | `520ff5b50299` |
| `.github/workflows/s23-50m-complement-xor-admission.yml` | `ed4a3daae4dc` |
| `.github/workflows/s23-50n-complement-partition-admission.yml` | `d0a87abde3b6` |
| `.github/workflows/s23-50o-integer-dag-admission.yml` | `68abae4f5f9e` |
| `.github/workflows/s23-50p-cancellation-partition-admission.yml` | `771c09fd58a5` |
| `.github/workflows/s23-50q-xor-mul-factoring-admission.yml` | `678f7d30b36b` |
| `.github/workflows/s23-50r-nested-cancellation-admission.yml` | `2f9260f2f876` |
| `.github/workflows/s23-50s-boolean-xor-admission.yml` | `768eb7be7117` |
| `.github/workflows/s23-50t-complement-xor-admission.yml` | `4af9ac1e6f58` |
| `.github/workflows/s23-50u-demorgan-xor-admission.yml` | `dcccdbcf09b6` |
| `.github/workflows/s23-50v-complement-xor-sub-admission.yml` | `8e31d6a9894f` |
| `.github/workflows/s23-50w-interaction-audit.yml` | `005a910996c6` |
| `.github/workflows/s23-50x-wrapping-admission.yml` | `ddcb2c300f04` |
| `.github/workflows/s23-50y-boolean-dag-admission.yml` | `586701ac1085` |
| `.github/workflows/s23-50z-native-matrix-admission.yml` | `cc4d2df93901` |
| `.github/workflows/s23-51-generated-typeid-cost.yml` | `94c16c3c3bee` |
| `.github/workflows/s23-6-extended-compare.yml` | `fcfe402932e1` |
| `.github/workflows/s23-7-extended-literal.yml` | `e70a8a9aee22` |
| `.github/workflows/s23-8-zero-comparison.yml` | `d2856cdb6edf` |
| `.github/workflows/s23-9-matchpd-opposing-shifts.yml` | `b312d7c5dd35` |
