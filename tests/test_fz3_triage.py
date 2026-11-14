"""End-to-end synthetic FZ-3 persistent history tests; no GitHub/network needed."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fz3_triage as tool  # noqa: E402

SHA1 = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "abcdef0123456789abcdef0123456789abcdef01"
URL1 = "https://github.com/bingordo/compiler-ci/actions/runs/123"
URL2 = "https://github.com/bingordo/compiler-ci/actions/runs/124"


class TriageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "campaign"
        self.history = self.root / "history"
        self.out = self.root / "triage"
        self.report.mkdir()

    def row(self, *, sink="gas", a="5", b="7", expected="12",
            actual="4294967294", occurrences="2", first_seed="1"):
        index = 0 if sink == "gas" else 1
        dirname = self.report / f"failure-{index:03d}"
        dirname.mkdir(parents=True, exist_ok=True)
        for name in ("before.ir", "compiled.ir", "minimized.ir", "replay.txt"):
            (dirname / name).write_text(f"verified test fixture {name}\n", encoding="utf-8")
        return dict(
            mode="raw", sink=sink, a=a, b=b, expected=expected,
            actual=actual, occurrences=occurrences, first_profile="linear-pure",
            first_seed=first_seed, probes="13", removed_instructions="2",
            before_ir=f"failure-{index:03d}/before.ir",
            minimized_ir=f"failure-{index:03d}/minimized.ir",
        )

    def report_rows(self, rows, *, sha=SHA1, injected="false"):
        status = "native-wrong-value" if rows else "no-mismatch"
        (self.report / "summary.txt").write_text(
            f"status={status}\nsource_sha={sha}\ninjected={injected}\n"
            f"unique_failures={len(rows)}\nfirst_seed=1\n",
            encoding="utf-8")
        tool.write_tsv(self.report / "fingerprints.tsv", tool.CAMPAIGN_COLUMNS, rows)

    def run_triage(self, *, sha=SHA1, token="123.1", url=URL1):
        return tool.triage(self.report, self.history, self.out, sha, token, url)

    def test_no_mismatch_runs_are_idempotently_registered(self):
        self.report_rows([])
        self.assertEqual(self.run_triage(), {
            "new": 0, "known": 0, "processed": 1, "replayed": 0,
        })
        self.assertEqual(len(tool.read_tsv(self.history / "fingerprints.tsv",
                                           tool.HISTORY_COLUMNS)), 0)
        self.assertEqual(self.run_triage()["replayed"], 1)
        self.assertEqual(len(tool.read_tsv(self.history / "processed_runs.tsv",
                                           tool.PROCESSED_COLUMNS)), 1)
        self.assertEqual(self.run_triage(token="124.1", url=URL2)["processed"], 1)
        self.assertEqual(len(tool.read_tsv(self.history / "processed_runs.tsv",
                                           tool.PROCESSED_COLUMNS)), 2)

    def test_new_then_known_signature_merges_observations_not_issues(self):
        first = self.row()
        self.report_rows([first])
        self.assertEqual(self.run_triage()["new"], 1)
        issue = self.out / f"issue-{tool.fingerprint_id(first)}.md"
        self.assertTrue(issue.is_file())
        self.assertIn("FZ3-FP:", issue.read_text())
        self.assertIn("genuine, non-injected", issue.read_text())
        new_rows = tool.read_tsv(self.out / "new.tsv", tool.NEW_COLUMNS)
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0]["issue_body"], issue.name)
        self.assertEqual(self.run_triage()["replayed"], 1)
        # A distinct compiler revision and run with identical actual symptoms
        # increments recurrence but never generates another issue candidate.
        second = self.row(occurrences="3", first_seed="4242")
        self.report_rows([second], sha=SHA2)
        self.assertEqual(self.run_triage(sha=SHA2, token="124.1", url=URL2)["known"], 1)
        self.assertEqual(tool.read_tsv(self.out / "new.tsv", tool.NEW_COLUMNS), [])
        hist = tool.read_tsv(self.history / "fingerprints.tsv", tool.HISTORY_COLUMNS)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["first_sha"], SHA1)
        self.assertEqual(hist[0]["last_sha"], SHA2)
        self.assertEqual(hist[0]["first_run"], URL1)
        self.assertEqual(hist[0]["runs"], "2")
        self.assertEqual(hist[0]["observations"], "5")
        self.assertEqual(hist[0]["first_seed"], "1")

    def test_both_sinks_are_distinct_even_with_same_wrong_value(self):
        a, b = self.row(sink="gas"), self.row(sink="elf")
        self.report_rows([a, b])
        self.assertEqual(self.run_triage()["new"], 2)
        keys = tool.read_tsv(self.history / "fingerprints.tsv", tool.HISTORY_COLUMNS)
        self.assertNotEqual(keys[0]["id"], keys[1]["id"])
        self.assertEqual(len(tool.read_tsv(self.out / "new.tsv",
                                           tool.NEW_COLUMNS)), 2)

    def test_injected_or_wrong_source_never_reaches_persistent_history(self):
        self.report_rows([self.row()], injected="true")
        with self.assertRaisesRegex(ValueError, "NEVER"):
            self.run_triage()
        self.assertFalse((self.history / "fingerprints.tsv").exists())
        self.report_rows([self.row()], sha=SHA2)
        with self.assertRaisesRegex(ValueError, "source SHA"):
            self.run_triage()
        self.assertFalse((self.history / "fingerprints.tsv").exists())

    def test_report_with_wrong_values_or_missing_ir_is_rejected(self):
        row = self.row(actual="12")
        self.report_rows([row])
        with self.assertRaisesRegex(ValueError, "not a wrong-value"):
            self.run_triage()
        row = self.row()
        row["minimized_ir"] = "../untrusted/minimized.ir"
        self.report_rows([row])
        with self.assertRaisesRegex(ValueError, "invalid artifact path"):
            self.run_triage()
        self.assertFalse((self.history / "processed_runs.tsv").exists())

    def test_unknown_or_duplicate_run_token_replay_is_not_silently_rewritten(self):
        self.report_rows([self.row()])
        self.run_triage()
        self.report_rows([self.row(actual="10")])
        with self.assertRaisesRegex(ValueError, "different report content"):
            self.run_triage()
        with self.assertRaisesRegex(ValueError, "run token"):
            self.run_triage(token="not-a-run")
        history = tool.read_tsv(self.history / "fingerprints.tsv",
                                tool.HISTORY_COLUMNS)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["actual"], "4294967294")

    def test_campaign_duplicate_fingerprints_fail_closed(self):
        x = self.row()
        self.report_rows([x, x.copy()])
        with self.assertRaisesRegex(ValueError, "duplicate exact fingerprint"):
            self.run_triage()
        self.assertFalse((self.history / "fingerprints.tsv").exists())

    def test_budget_truncation_reports_preserve_genuine_findings(self):
        rows = [self.row()]
        self.report_rows(rows)
        with (self.report / "summary.txt").open("a") as handle:
            handle.write("stopped_at_budget=true\n")
        result = self.run_triage()
        self.assertEqual(result["new"], 1)
        self.assertEqual(len(tool.read_tsv(self.history / "fingerprints.tsv",
                                           tool.HISTORY_COLUMNS)), 1)


if __name__ == "__main__":
    unittest.main()
