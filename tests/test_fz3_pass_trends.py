"""Persistent native pass-stage activity trends; explicit visit/edit semantics."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fz3_triage as base  # noqa: E402
import fz3_pass_trends as trends  # noqa: E402

SHA1 = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "abcdef0123456789abcdef0123456789abcdef01"
URL1 = "https://github.com/bingordo/compiler-ci/actions/runs/123"
URL2 = "https://github.com/bingordo/compiler-ci/actions/runs/124"


class TrendsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.campaign = self.root / "campaign"
        self.history = self.root / "history"
        self.triage = self.root / "triage"
        self.output = self.root / "output"
        self.campaign.mkdir()

    def rows(self, visits=3, edits=1):
        return [{
            "mode": mode, "stage": stage,
            "visits": str(visits if mode == "disabled" else 0),
            "edits": str(edits if mode == "disabled" and stage == "core.dce" else 0),
        } for mode in trends.MODES for stage in trends.STAGES]

    def report(self, *, sha=SHA1, rows=None, injected=False, truncated=False):
        if rows is None:
            rows = self.rows()
        (self.campaign / "summary.txt").write_text(
            "status=no-mismatch\n"
            f"source_sha={sha}\ninjected={str(injected).lower()}\n"
            f"stopped_at_budget={str(truncated).lower()}\n"
            "cases=5\nunique_failures=0\n",
            encoding="utf-8")
        base.write_tsv(self.campaign / "fingerprints.tsv",
                       base.CAMPAIGN_COLUMNS, [])
        base.write_tsv(self.campaign / "pass_coverage.tsv",
                       trends.CAMPAIGN_COLUMNS, rows)

    def reconcile(self, sha=SHA1, token="123.1", url=URL1):
        base.triage(self.campaign, self.history, self.triage, sha, token, url)
        return trends.record(self.campaign, self.history, self.output,
                             sha, token, url)

    def test_new_run_records_complete_stage_matrix_and_exact_totals(self):
        self.report()
        result = self.reconcile()
        self.assertEqual(result, {
            "rows": 132, "edited_stages": 1, "replayed": 0, "truncated": 0,
        })
        history = base.read_tsv(self.history / "pass_runs.tsv", trends.RUN_COLUMNS)
        self.assertEqual(len(history), 132)
        self.assertTrue(all(row["source_sha"] == SHA1 for row in history))
        total = base.read_tsv(self.history / "pass_totals.tsv",
                              trends.TOTAL_COLUMNS)
        self.assertEqual(len(total), 132)
        edited = [r for r in total if r["mode"] == "disabled"
                  and r["stage"] == "core.dce"][0]
        self.assertEqual(edited["runs"], "1")
        self.assertEqual(edited["visits"], "3")
        self.assertEqual(edited["edits"], "1")
        self.assertEqual(edited["runs_edited"], "1")
        self.assertIn("not** certify", (self.output / "pass_trend.md").read_text())

    def test_replay_does_not_double_count_and_cross_sha_trend_accumulates(self):
        self.report()
        self.reconcile()
        self.assertEqual(self.reconcile()["replayed"], 1)
        self.report(sha=SHA2, rows=self.rows(visits=2, edits=0), truncated=True)
        result = self.reconcile(SHA2, "124.1", URL2)
        self.assertEqual(result["truncated"], 1)
        total = base.read_tsv(self.history / "pass_totals.tsv",
                              trends.TOTAL_COLUMNS)
        dce = [r for r in total if r["mode"] == "disabled"
               and r["stage"] == "core.dce"][0]
        self.assertEqual(dce["runs"], "2")
        self.assertEqual(dce["visits"], "5")
        self.assertEqual(dce["edits"], "1")
        self.assertEqual(dce["runs_edited"], "1")
        self.assertEqual(len(base.read_tsv(self.history / "pass_runs.tsv",
                                           trends.RUN_COLUMNS)), 264)
        self.assertEqual(self.reconcile(SHA2, "124.1", URL2)["replayed"], 1)

    def test_injected_report_is_never_accepted(self):
        self.report(injected=True)
        with self.assertRaisesRegex(ValueError, "injected"):
            self.reconcile()
        self.assertFalse((self.history / "pass_runs.tsv").exists())

    def test_missing_unknown_duplicate_or_invalid_counts_fail_closed(self):
        self.report(rows=self.rows()[:-1])
        with self.assertRaisesRegex(ValueError, "expected 132"):
            self.reconcile()
        changed = self.rows()
        changed[0]["stage"] = "core.unknown_new_pass"
        self.report(rows=changed)
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            self.reconcile()
        changed = self.rows()
        changed[0]["stage"] = changed[1]["stage"]
        self.report(rows=changed)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.reconcile()
        changed = self.rows()
        changed[0]["edits"] = "999"
        self.report(rows=changed)
        with self.assertRaisesRegex(ValueError, "invalid pass activity"):
            self.reconcile()
        self.assertFalse((self.history / "pass_runs.tsv").exists())

    def test_stage_visit_denominators_cannot_disagree(self):
        changed = self.rows()
        for row in changed:
            if row["mode"] == "disabled" and row["stage"] == "core.sccp":
                row["visits"] = "2"
        self.report(rows=changed)
        with self.assertRaisesRegex(ValueError, "inconsistent core"):
            self.reconcile()
        self.assertFalse((self.history / "pass_runs.tsv").exists())

    def test_reprocessing_same_run_with_altered_report_does_not_rewrite_history(self):
        self.report()
        self.reconcile()
        self.report(rows=self.rows(visits=2))
        with self.assertRaisesRegex(ValueError, "changed native pass activity"):
            self.reconcile()
        self.assertEqual(len(base.read_tsv(self.history / "pass_runs.tsv",
                                           trends.RUN_COLUMNS)), 132)


if __name__ == "__main__":
    unittest.main()
