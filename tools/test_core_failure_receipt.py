import copy
import unittest
from core_failure_receipt import classify


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.report = {"source_sha": "a" * 40, "suites": [
            {"suite": "cargo-build", "status": "passed"},
            {"suite": "cargo-tests", "status": "failed", "exit_code": 101,
             "rustc_error_codes": [], "failed_test_case_ids": ["private-hash"],
             "test_counts": {"passed": 100, "failed": 2, "ignored": 3},
             "log": "private/path"}]}
        self.sha = "a" * 40

    def test_completed_failure(self):
        self.assertEqual(classify(self.report, self.sha, "run"),
            {"source_sha": self.sha, "run_url": "run", "passed": 100,
             "failed": 2, "ignored": 3})

    def test_never_reuse_wrong_revision(self):
        self.assertIsNone(classify(self.report, "b" * 40, "run"))

    def test_never_reuse_build_failure(self):
        self.report["suites"][0]["status"] = "failed"
        self.assertIsNone(classify(self.report, self.sha, "run"))

    def test_reject_incomplete_or_infrastructure_failure(self):
        for changes in ({"status": "passed"}, {"exit_code": 137},
                        {"rustc_error_codes": ["E0425"]}, {"failed_test_case_ids": []},
                        {"test_counts": None}, {"test_counts": {"passed": 0, "failed": 2, "ignored": 0}},
                        {"test_counts": {"passed": 100, "failed": 0, "ignored": 0}},
                        {"test_counts": {"passed": 100, "failed": 2}}):
            with self.subTest(changes=changes):
                report = copy.deepcopy(self.report)
                report["suites"][1].update(changes)
                self.assertIsNone(classify(report, self.sha, "run"))


if __name__ == "__main__":
    unittest.main()
