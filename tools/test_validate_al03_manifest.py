"""Negative controls for the permanently versioned AL-03 proof manifest."""
import unittest

from validate_al03_manifest import ManifestError, validate


class AL03ManifestTests(unittest.TestCase):
    def setUp(self):
        self.name = "al03_sample_concrete"
        self.proof = "al03_sample_smt"
        self.marker = "AL03_SAMPLE changed_passes=2 unsat=2 sat_phi=2 concrete=128 verdict=PROVEN"
        self.manifest = {
            "schema_version": 1,
            "suites": [{"ordinary": self.name, "proof": self.proof,
                        "evidence": self.marker}],
        }
        self.source = (
            "#[test]\nfn al03_sample_concrete() {}\n"
            "#[test]\n#[ignore = \"requires Z3\"]\nfn al03_sample_smt() {}\n"
        )
        self.concrete = (
            "running 1 test\n"
            "test alive_lite_typeid_wide::al03_sample_concrete ... ok\n"
            "test result: ok. 1 passed; 0 failed\n"
        )
        self.z3 = (
            "running 1 test\n"
            f"test alive_lite_typeid_wide::{self.proof} ... {self.marker}\n"
            "ok\n"
            "test result: ok. 1 passed; 0 failed\n"
        )
        self.sha = "a"*40

    def check(self, **changes):
        return validate(
            changes.get("manifest", self.manifest),
            changes.get("source", self.source),
            changes.get("concrete", self.concrete),
            changes.get("proof", self.z3),
            self.sha,
        )

    def test_accepts_concrete_and_exact_formal_witness(self):
        r = self.check()
        self.assertEqual(r["suites"], 1)
        self.assertEqual(r["real_unsat_witnesses"], 2)
        self.assertEqual(r["sat_negative_controls"], 2)
        self.assertEqual(r["independent_concrete_cases_explicit"], 128)

    def test_missing_concrete_regression_fails(self):
        with self.assertRaises(ManifestError):
            self.check(concrete=self.concrete.replace(" ... ok", " ... ignored"))

    def test_missing_proof_regression_fails(self):
        with self.assertRaises(ManifestError):
            self.check(proof=self.z3.replace(" ... AL03_SAMPLE", " ... ignored"))

    def test_changed_proof_count_fails(self):
        with self.assertRaises(ManifestError):
            self.check(proof=self.z3.replace("unsat=2", "unsat=1"))

    def test_missing_sat_controls_fails(self):
        with self.assertRaises(ManifestError):
            self.check(proof=self.z3.replace("sat_phi=2", "sat_phi=0"))

    def test_new_source_test_must_join_manifest(self):
        with self.assertRaises(ManifestError):
            self.check(source=self.source + "\n#[test]\nfn al03_new_concrete() {}\n")

    def test_missing_source_test_fails_closed(self):
        with self.assertRaises(ManifestError):
            self.check(source=self.source.replace(
                "#[test]\n#[ignore = \"requires Z3\"]\nfn al03_sample_smt() {}\n", ""
            ))

    def test_proof_must_finish_ok(self):
        with self.assertRaises(ManifestError):
            self.check(proof=self.z3.replace("\nok\n", "\nFAILED\n"))

    def test_unmanifested_proof_fails(self):
        with self.assertRaises(ManifestError):
            self.check(proof=self.z3.replace("test result:", 
                "test alive_lite_typeid_wide::al03_unexpected ... AL03_EXTRA unsat=1 sat_phi=1\nok\ntest result:"))

    def test_old_manifest_cannot_misclassify_ignored_proofs(self):
        with self.assertRaises(ManifestError):
            self.check(source=self.source.replace("#[ignore = \"requires Z3\"]\n", ""))


if __name__ == "__main__":
    unittest.main()
