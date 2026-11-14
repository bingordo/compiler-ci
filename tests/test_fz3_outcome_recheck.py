"""Integration checks for conservative archive/recheck of FZ-3 outcome witnesses."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fz3_triage as base  # noqa: E402
import fz3_outcome_triage as v2  # noqa: E402
import fz3_outcome_recheck as recheck  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
URL = "https://github.com/bingordo/compiler-ci/actions/runs/123"


class OutcomeArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.report = root / "campaign"
        self.history = root / "history"
        self.triage = root / "triage"
        self.output = root / "recheck"
        self.oracle = root / "fake-oracle.py"
        self.report.mkdir()
        self.history.mkdir()
        self.oracle.write_text(
            "#!/usr/bin/env python3\n"
            "print('OUTCOMES_V1 a=5 b=0 expected=sigfpe observed=value:5')\n"
        )
        self.oracle.chmod(0o755)

    def fixture(self):
        directory = self.report / "outcome-000"
        directory.mkdir(exist_ok=True)
        for file in ("before.ir", "compiled.ir", "minimized.ir"):
            (directory / file).write_text(f"fixture {file}\n")
        (directory / "replay.txt").write_text(
            f"source_sha={SHA}\nmode=raw\nsink=gas\n"
            "expected_outcome=sigfpe\nobserved_outcome=value:5\n"
            "--expected-outcome sigfpe --observed-outcome value:5\n"
        )
        row = {
            "schema_version": "2", "mode": "raw", "sink": "gas", "a": "5",
            "b": "0", "expected": "sigfpe", "actual": "value:5",
            "occurrences": "1", "first_profile": "signed-div-alias",
            "first_seed": "0", "probes": "2", "removed_instructions": "1",
            "before_ir": "outcome-000/before.ir",
            "minimized_ir": "outcome-000/minimized.ir",
        }
        (self.report / "summary.txt").write_text(
            f"status=no-mismatch\nsource_sha={SHA}\ninjected=false\n"
            "unique_failures=0\noutcome_unique_failures=1\n"
            "outcome_duplicates=0\nevaluations=10\nmax_unique=4\n"
        )
        base.write_tsv(self.report / "fingerprints.tsv", base.CAMPAIGN_COLUMNS, [])
        base.write_tsv(self.report / "outcome_fingerprints_v2.tsv", v2.V2_COLUMNS, [row])
        _, digest = base.verify_campaign(self.report, SHA)
        base.write_tsv(self.history / "processed_runs.tsv",
                       base.PROCESSED_COLUMNS, [{
                           "run_token": "123.1", "run_url": URL,
                           "source_sha": SHA, "report_digest": digest
                       }])
        v2.reconcile(self.report, self.history, self.triage, SHA, "123.1", URL)
        return row

    def run_recheck(self, *, oracle=None):
        return recheck.recheck(self.report, self.history, self.triage, self.output,
                               oracle or self.oracle, SHA, "123.1", URL)

    def test_exact_live_replay_before_archiving(self):
        row = self.fixture()
        result = self.run_recheck()
        self.assertEqual(result["archived"], 1)
        archived = recheck.witness_path(self.history, v2.fingerprint_id(row))
        self.assertEqual(archived.read_text(), "fixture minimized.ir\n")
        statuses = base.read_tsv(self.history / "outcome_rechecks_v2.tsv",
                                 recheck.STATUS_COLUMNS)
        self.assertEqual(statuses[0]["state"], "reproduces")
        self.assertEqual(self.run_recheck()["archived"], 0,
                         "idempotent replay must not create new archives")
        self.assertEqual(archived.read_text(), "fixture minimized.ir\n")

    def test_wrong_oracle_result_cannot_archive(self):
        row = self.fixture()
        self.oracle.write_text("#!/usr/bin/env python3\n"
                               "print('OUTCOMES_V1 a=6 b=0 expected=sigfpe observed=value:5')\n")
        with self.assertRaisesRegex(ValueError, "did not reproduce"):
            self.run_recheck()
        self.assertFalse(recheck.witness_path(self.history, v2.fingerprint_id(row)).exists())
        self.assertFalse((self.history / "outcome_rechecks_v2.tsv").exists())

    def test_nonmatching_pin_never_claims_fix(self):
        row = self.fixture()
        self.run_recheck()
        ident = v2.fingerprint_id(row)
        status, detail = recheck.classify(self.oracle,
                                          recheck.witness_path(self.history, ident),
                                          row, timeout=2)
        self.assertEqual(status, "reproduces")
        self.oracle.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
        status, detail = recheck.classify(self.oracle,
                                          recheck.witness_path(self.history, ident),
                                          row, timeout=2)
        self.assertEqual(status, "unavailable",
                         "exit 1 must not claim agreement or a fix")
        self.assertIn("oracle exited", detail)

    def test_oracle_errors_are_unavailable(self):
        row = self.fixture()
        self.oracle.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n")
        status, _ = recheck.classify(self.oracle, self.report /
                                     row["minimized_ir"], row, 2)
        self.assertEqual(status, "unavailable")
        self.oracle.write_text("#!/usr/bin/env python3\n"
                               "print('something else')\n")
        status, _ = recheck.classify(self.oracle, self.report /
                                     row["minimized_ir"], row, 2)
        self.assertEqual(status, "unavailable")

    def test_unpinned_reports_distinguish_agreement_changed_error_and_reference_drift(self):
        row = self.fixture()
        self.run_recheck()
        witness = recheck.witness_path(self.history, v2.fingerprint_id(row))
        for observed, expected_status in (
            ("sigfpe", "agrees"),
            ("value:6", "changed-mismatch"),
            ("value:5", "reproduces"),
        ):
            with self.subTest(observed=observed):
                self.oracle.write_text(
                    "#!/usr/bin/env python3\\n"
                    f"print('OUTCOMES_V1 a=5 b=0 expected=sigfpe observed={observed}')\\n"
                )
                status, _ = recheck.classify(self.oracle, witness, row, 2)
                self.assertEqual(status, expected_status)
        self.oracle.write_text(
            "#!/usr/bin/env python3\\n"
            "print('OUTCOMES_V1 a=5 b=0 expected=value:0 observed=value:0')\\n"
        )
        status, detail = recheck.classify(self.oracle, witness, row, 2)
        self.assertEqual(status, "unavailable")
        self.assertIn("reference drift", detail)

    def next_run(self, sha, run_number):
        """Register one real-source-identified synthetic clean campaign."""
        token = f"{run_number}.1"
        url = f"https://github.com/bingordo/compiler-ci/actions/runs/{run_number}"
        (self.report / "summary.txt").write_text(
            f"status=no-mismatch\\nsource_sha={sha}\\ninjected=false\\n"
            "unique_failures=0\\noutcome_unique_failures=0\\n"
            "outcome_duplicates=0\\nevaluations=10\\nmax_unique=4\\n"
        )
        base.write_tsv(self.report / "outcome_fingerprints_v2.tsv",
                       v2.V2_COLUMNS, [])
        _, digest = base.verify_campaign(self.report, sha)
        receipts = base.read_tsv(self.history / "processed_runs.tsv",
                                 base.PROCESSED_COLUMNS)
        receipts.append({
            "run_token": token, "run_url": url, "source_sha": sha,
            "report_digest": digest,
        })
        base.write_tsv(self.history / "processed_runs.tsv",
                       base.PROCESSED_COLUMNS, receipts)
        v2.reconcile(self.report, self.history, self.triage, sha, token, url)
        return recheck.recheck(self.report, self.history, self.triage,
                               self.output, self.oracle, sha, token, url)

    def test_distinct_revision_fix_evidence_regression_and_no_duplicate_events(self):
        row = self.fixture()
        self.run_recheck()
        sha2 = "abcdef0123456789abcdef0123456789abcdef01"
        sha3 = "23456789abcdef0123456789abcdef01234567"
        sha4 = "3456789abcdef0123456789abcdef012345678"
        self.oracle.write_text(
            "#!/usr/bin/env python3\\n"
            "print('OUTCOMES_V1 a=5 b=0 expected=sigfpe observed=sigfpe')\\n"
        )
        result = self.next_run(sha2, 124)
        self.assertEqual(result["agrees"], 1)
        events = base.read_tsv(self.output / "outcome_events_v2.tsv",
                               recheck.EVENT_COLUMNS)
        self.assertEqual([r["event"] for r in events], ["suspected-fix"])
        evidence = base.read_tsv(self.history / "outcome_agreements_v2.tsv",
                                 recheck.AGREEMENT_COLUMNS)
        self.assertEqual(evidence[0]["first_agree_sha"], sha2)
        self.assertEqual(evidence[0]["second_agree_sha"], "")
        result = self.next_run(sha3, 125)
        self.assertEqual(result["agrees"], 1)
        events = base.read_tsv(self.output / "outcome_events_v2.tsv",
                               recheck.EVENT_COLUMNS)
        self.assertEqual([r["event"] for r in events],
                         ["second-revision-agreement"])
        evidence = base.read_tsv(self.history / "outcome_agreements_v2.tsv",
                                 recheck.AGREEMENT_COLUMNS)
        self.assertEqual(evidence[0]["second_agree_sha"], sha3)
        # A repeated run cannot produce another issue transition.
        replay = recheck.recheck(self.report, self.history, self.triage,
                                self.output, self.oracle, sha3, "125.1",
                                "https://github.com/bingordo/compiler-ci/actions/runs/125")
        self.assertEqual(replay["agrees"], 0)
        self.assertEqual(base.read_tsv(self.output / "outcome_events_v2.tsv",
                                       recheck.EVENT_COLUMNS), [])
        self.oracle.write_text(
            "#!/usr/bin/env python3\\n"
            "print('OUTCOMES_V1 a=5 b=0 expected=sigfpe observed=value:5')\\n"
        )
        result = self.next_run(sha4, 126)
        self.assertEqual(result["reproduces"], 1)
        events = base.read_tsv(self.output / "outcome_events_v2.tsv",
                               recheck.EVENT_COLUMNS)
        self.assertEqual([r["event"] for r in events], ["regression"])
        self.assertEqual(base.read_tsv(
            self.history / "outcome_agreements_v2.tsv",
            recheck.AGREEMENT_COLUMNS), [])

    def test_changed_minimized_ir_after_triage_cannot_silently_archive(self):
        self.fixture()
        (self.report / "outcome-000/minimized.ir").unlink()
        with self.assertRaisesRegex(ValueError, "witness"):
            self.run_recheck()
        self.assertFalse((self.history / "outcome_rechecks_v2.tsv").exists())


if __name__ == "__main__":
    unittest.main()
