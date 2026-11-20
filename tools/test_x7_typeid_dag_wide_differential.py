"""Deterministic source and evidence schema boundaries for the X7 wide oracle."""
import tempfile
from pathlib import Path
import unittest

from x7_typeid_dag_wide_differential import EDGE_VALUES, HARNESS, generated_cases


class WideDifferentialTests(unittest.TestCase):
    def test_corpus_stable_and_strict_source_count(self):
        root = Path(__file__).resolve().parents[1] / "compiler"
        a = generated_cases(root)
        b = generated_cases(root)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 70)
        self.assertEqual(sum(name.startswith("fuzz_") for name in a), 48)
        self.assertEqual(sum(name.startswith("source_") for name in a), 12)
        self.assertTrue(all(source.startswith("int ") for source in a.values()))
        self.assertIn("return x-(0-y);", a["expr_add_sub_neg"])

    def test_host_oracle_includes_integer_extremes_and_random_inputs(self):
        self.assertIn("-2147483647-1", EDGE_VALUES)
        self.assertIn("2147483647", EDGE_VALUES)
        self.assertIn("512", HARNESS)
        self.assertIn("int (*volatile function)(int,int)", HARNESS)


if __name__ == "__main__":
    unittest.main()
