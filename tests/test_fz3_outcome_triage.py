"""FZ-3 v2 trap-history validation, deduplication and fail-closed tests."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fz3_triage as base  # noqa: E402
import fz3_outcome_triage as v2  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "abcdef0123456789abcdef0123456789abcdef01"
URL = "https://github.com/bingordo/compiler-ci/actions/runs/123"
URL2 = "https://github.com/bingordo/compiler-ci/actions/runs/124"


class OutcomeTriageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.report = root / "report"
        self.history = root / "state"
        self.out = root / "out"
        self.report.mkdir()
        self.history.mkdir()

    def outcome(self, index=0, *, expected="sigfpe", actual="value:5",
                mode="raw", sink="gas", first_seed="1"):
        directory = self.report / f"outcome-{index:03}"
        directory.mkdir(exist_ok=True)
        row = {
            "schema_version": "2", "mode": mode, "sink": sink, "a": "5",
            "b": "0", "expected": expected, "actual": actual,
            "occurrences": "1", "first_profile": "signed-div-alias",
            "first_seed": first_seed, "probes": "3", "removed_instructions": "1",
            "before_ir": f"outcome-{index:03}/before.ir",
            "minimized_ir": f"outcome-{index:03}/minimized.ir",
        }
        for name in ("before.ir", "compiled.ir", "minimized.ir"):
            (directory / name).write_text(f"verified TypeId fixture {name}\n")
        (directory / "replay.txt").write_text(
            f"source_sha={SHA}\nmode={mode}\nsink={sink}\n"
            f"expected_outcome={expected}\nobserved_outcome={actual}\n"
            f"--expected-outcome {expected} --observed-outcome {actual}\n"
        )
        return row

    def set_report(self, rows, sha=SHA, *, injected="false"):
        status = "no-mismatch"  # v1 has no wrong-value findings
        (self.report / "summary.txt").write_text(
            f"status={status}\nsource_sha={sha}\ninjected={injected}\n"
            f"unique_failures=0\noutcome_unique_failures={len(rows)}\n"
            f"outcome_duplicates=0\nevaluations=12\nmax_unique=4\n"
        )
        base.write_tsv(self.report / "fingerprints.tsv", base.CAMPAIGN_COLUMNS, [])
        base.write_tsv(self.report / "outcome_fingerprints_v2.tsv", v2.V2_COLUMNS, rows)
        if injected == "false":
            _, digest = base.verify_campaign(self.report, sha)
        else:
            digest = hashlib.sha256(
                (self.report / "summary.txt").read_bytes() + b"\0" +
                (self.report / "fingerprints.tsv").read_bytes()
            ).hexdigest()
        base.write_tsv(self.history / "processed_runs.tsv", base.PROCESSED_COLUMNS,
                       [{"run_token": "123.1", "run_url": URL,
                         "source_sha": sha, "report_digest": digest}])

    def triage(self, *, sha=SHA, token="123.1", url=URL):
        return v2.reconcile(self.report, self.history, self.out, sha, token, url)

    def test_empty_and_idempotent(self):
        self.set_report([])
        self.assertEqual(self.triage()["new"], 0)
        self.assertEqual(self.triage()["replayed"], 1)
        self.assertEqual(v2.base.read_tsv(self.history / "outcome_fingerprints_v2.tsv",
                                          v2.HISTORY_COLUMNS), [])
        self.assertEqual(len(base.read_tsv(self.history / "processed_runs.tsv",
                                           base.PROCESSED_COLUMNS)), 1)

    def test_new_and_recurring_signature_no_duplicate_issue(self):
        row = self.outcome()
        self.set_report([row])
        self.assertEqual(self.triage()["new"], 1)
        ident = v2.fingerprint_id(row)
        self.assertTrue((self.out / f"outcome-issue-{ident}.md").is_file())
        self.assertEqual(self.triage()["replayed"], 1)
        self.assertEqual(len(v2.base.read_tsv(
            self.out / "outcome_new_v2.tsv", v2.NEW_COLUMNS)), 0)
        row["occurrences"] = "3"
        self.set_report([row], sha=SHA2)
        # Metadata for the second run must be a genuine registered v1 report.
        (self.report / "outcome-000/replay.txt").write_text(
            f"source_sha={SHA2}\nmode=raw\nsink=gas\n"
            "expected_outcome=sigfpe\nobserved_outcome=value:5\n"
            "--expected-outcome sigfpe --observed-outcome value:5\n"
        )
        _, digest = base.verify_campaign(self.report, SHA2)
        previous = base.read_tsv(self.history / "processed_runs.tsv",
                                 base.PROCESSED_COLUMNS)
        previous.append({"run_token": "124.1", "run_url": URL2,
                         "source_sha": SHA2, "report_digest": digest})
        base.write_tsv(self.history / "processed_runs.tsv", base.PROCESSED_COLUMNS,
                       previous)
        self.assertEqual(self.triage(sha=SHA2, token="124.1", url=URL2)["known"], 1)
        history = base.read_tsv(self.history / "outcome_fingerprints_v2.tsv",
                                v2.HISTORY_COLUMNS)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["runs"], "2")
        self.assertEqual(history[0]["observations"], "4")
        self.assertEqual(history[0]["first_sha"], SHA)
        self.assertEqual(history[0]["last_sha"], SHA2)

    def test_distinguish_opposite_traps_modes_and_sinks(self):
        rows = [
            self.outcome(0, expected="sigfpe", actual="value:5", sink="gas"),
            self.outcome(1, expected="value:5", actual="sigfpe", sink="gas"),
            self.outcome(2, expected="sigfpe", actual="value:5", sink="elf"),
        ]
        self.set_report(rows)
        self.assertEqual(self.triage()["new"], 3)
        history = base.read_tsv(self.history / "outcome_fingerprints_v2.tsv",
                                v2.HISTORY_COLUMNS)
        self.assertEqual(len({row["id"] for row in history}), 3)
        self.assertEqual(len(base.read_tsv(self.out / "outcome_new_v2.tsv",
                                           v2.NEW_COLUMNS)), 3)

    def test_injection_or_invalid_version_never_creates_v2_state(self):
        rows = [self.outcome()]
        self.set_report(rows, injected="true")
        with self.assertRaisesRegex(ValueError, "NEVER"):
            self.triage()
        self.assertFalse((self.history / "outcome_fingerprints_v2.tsv").exists())
        self.set_report(rows)
        rows[0]["schema_version"] = "3"
        base.write_tsv(self.report / "outcome_fingerprints_v2.tsv",
                       v2.V2_COLUMNS, rows)
        with self.assertRaisesRegex(ValueError, "schema version"):
            self.triage()
        self.assertFalse((self.history / "outcome_processed_runs_v2.tsv").exists())

    def test_corrupt_counts_and_unsupported_or_equal_outcomes_rejected(self):
        for expected, actual in (
            ("value:5", "value:5"), ("sigsegv", "value:1"),
            ("sigfpe", "value:4294967296"), ("sigfpe", "value:05")
        ):
            with self.subTest(expected=expected, actual=actual):
                row = self.outcome(expected=expected, actual=actual)
                self.set_report([row])
                with self.assertRaisesRegex(ValueError, "outcome fingerprint"):
                    self.triage()
        row = self.outcome()
        row["occurrences"] = "20"
        self.set_report([row])
        with self.assertRaisesRegex(ValueError, "more outcome observations"):
            self.triage()
        self.assertFalse((self.history / "outcome_fingerprints_v2.tsv").exists())

    def test_unsafe_paths_and_forged_replay_fail_closed(self):
        row = self.outcome()
        row["minimized_ir"] = "../outcome-000/minimized.ir"
        self.set_report([row])
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.triage()
        row = self.outcome()
        self.set_report([row])
        (self.report / "outcome-000/replay.txt").write_text(
            "source_sha=bad\n--expected-outcome value:5\n"
        )
        with self.assertRaisesRegex(ValueError, "replay does not match"):
            self.triage()
        self.assertFalse((self.history / "outcome_processed_runs_v2.tsv").exists())

    def test_v1_registration_required_and_report_replay_is_immutable(self):
        row = self.outcome()
        self.set_report([row])
        (self.history / "processed_runs.tsv").unlink()
        with self.assertRaisesRegex(ValueError, "v1 native campaign"):
            self.triage()
        self.set_report([row])
        self.triage()
        row["actual"] = "value:6"
        self.outcome(actual="value:6")
        self.set_report([row])
        with self.assertRaisesRegex(ValueError, "changed outcome report"):
            self.triage()

    def test_partial_history_initialization_rejected(self):
        self.set_report([])
        (self.history / "outcome_fingerprints_v2.tsv").write_text(
            "\t".join(v2.HISTORY_COLUMNS) + "\n"
        )
        with self.assertRaisesRegex(ValueError, "partial v2"):
            self.triage()


if __name__ == "__main__":
    unittest.main()
