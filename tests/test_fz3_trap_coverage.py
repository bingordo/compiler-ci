"""Fail-closed test cases for FZ-3's observed signed trap coverage gate."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fz3_triage as base  # noqa: E402
import fz3_trap_coverage as coverage  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"


class TrapCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.report = Path(self.temp.name)
        self.rows = [
            dict(profile=profile, mode=mode, sink=sink,
                 defined="1", zero_sigfpe="1", overflow_sigfpe="1")
            for profile, mode, sink in sorted(coverage.EXPECTED)
        ]
        self.report_ok()

    def report_ok(self, *, first_seed="100", injected="false",
                  sha=SHA, budget="false"):
        (self.report / "summary.txt").write_text(
            f"status=no-mismatch\nsource_sha={sha}\ninjected={injected}\n"
            f"first_seed={first_seed}\nseeds_per_profile=2\n"
            f"max_evaluations=240\nevaluations=240\ncases=10\n"
            f"stopped_at_budget={budget}\n", encoding="utf-8"
        )
        base.write_tsv(self.report / "trap_coverage.tsv", coverage.COLUMNS, self.rows)

    def test_accepts_all_required_native_cases_without_guessing(self):
        self.assertEqual(coverage.verify(self.report, SHA), {
            "verified_profile_modes_sinks": 16,
            "defined_executions": 16, "zero_sigfpe_executions": 16,
            "overflow_sigfpe_executions": 16,
        })

    def test_all_missing_coverage_or_missing_one_mode_is_rejected(self):
        (self.report / "trap_coverage.tsv").unlink()
        with self.assertRaisesRegex(ValueError, "missing native trap coverage"):
            coverage.verify(self.report, SHA)
        self.rows.pop()
        self.report_ok()
        with self.assertRaisesRegex(ValueError, "wrong number"):
            coverage.verify(self.report, SHA)

    def test_swapped_repeated_and_forged_rows_fail_closed(self):
        self.rows[0]["profile"] = "linear-pure"
        self.report_ok()
        with self.assertRaisesRegex(ValueError, "unknown or duplicate"):
            coverage.verify(self.report, SHA)
        self.rows[0]["profile"] = self.rows[1]["profile"]
        self.rows[0]["mode"] = self.rows[1]["mode"]
        self.rows[0]["sink"] = self.rows[1]["sink"]
        self.report_ok()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            coverage.verify(self.report, SHA)

    def test_defined_and_each_independent_trap_category_are_required(self):
        for name in ("defined", "zero_sigfpe", "overflow_sigfpe"):
            for incorrect in ("0", "2", "01", "-1", "not-available"):
                with self.subTest(category=name, incorrect=incorrect):
                    self.rows[3][name] = incorrect
                    self.report_ok()
                    with self.assertRaisesRegex(ValueError, name):
                        coverage.verify(self.report, SHA)
                    self.rows[3][name] = "1"

    def test_injected_wrong_source_or_wrong_seeds_are_never_credited(self):
        for kwargs, error in (
            ({"injected": "true"}, "injected"),
            ({"sha": "a" * 40}, "source SHA mismatch"),
            ({"first_seed": "101"}, "even seed"),
            ({"budget": "true"}, "stopped_at_budget"),
        ):
            with self.subTest(kwargs=kwargs):
                self.report_ok(**kwargs)
                with self.assertRaisesRegex(ValueError, error):
                    coverage.verify(self.report, SHA)
        self.report_ok()
        with self.assertRaisesRegex(ValueError, "invalid exact compiler SHA"):
            coverage.verify(self.report, "main")


if __name__ == "__main__":
    unittest.main()
