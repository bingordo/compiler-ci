"""FZ-3 archived native IR lifecycle regression tests (no network required)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fz3_triage as base  # noqa: E402
import fz3_recheck as check  # noqa: E402

SHA1 = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "abcdef0123456789abcdef0123456789abcdef01"
SHA3 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
SHA4 = "cccccccccccccccccccccccccccccccccccccccc"
RUN = "https://github.com/bingordo/compiler-ci/actions/runs/"


class ArchivedWitnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.campaign = self.root / "campaign"
        self.history = self.root / "history"
        self.triage_out = self.root / "triage"
        self.recheck_out = self.root / "recheck"
        self.campaign.mkdir()
        self.oracle = self.root / "oracle.py"
        self.behavior = self.root / "behavior"
        self.behavior.write_text("reproduces")
        self.oracle.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            "behavior = Path(__file__).with_name('behavior').read_text()\n"
            "if behavior == 'agreement': sys.exit(1)\n"
            "if behavior == 'error': sys.exit(2)\n"
            "if behavior == 'timeout':\n"
            "    import time\n"
            "    time.sleep(2)\n"
            "if behavior == 'reference-shift':\n"
            "    print('WRONG_VALUE a=5 b=7 expected=13 observed=4294967294')\n"
            "elif behavior == 'changed':\n"
            "    print('WRONG_VALUE a=5 b=7 expected=12 observed=111')\n"
            "else:\n"
            "    print('WRONG_VALUE a=5 b=7 expected=12 observed=4294967294')\n"
        )
        self.oracle.chmod(0o755)

    def row(self):
        folder = self.campaign / "failure-000"
        folder.mkdir(exist_ok=True)
        for name in ("before.ir", "compiled.ir", "minimized.ir", "replay.txt"):
            (folder / name).write_text(f"fixture {name}\n")
        return {
            "mode": "raw", "sink": "gas", "a": "5", "b": "7",
            "expected": "12", "actual": "4294967294",
            "occurrences": "2", "first_profile": "linear-pure",
            "first_seed": "1", "probes": "17", "removed_instructions": "2",
            "before_ir": "failure-000/before.ir",
            "minimized_ir": "failure-000/minimized.ir",
        }

    def report(self, rows, sha):
        status = "native-wrong-value" if rows else "no-mismatch"
        (self.campaign / "summary.txt").write_text(
            f"status={status}\nsource_sha={sha}\ninjected=false\n"
            f"unique_failures={len(rows)}\n",
        )
        base.write_tsv(
            self.campaign / "fingerprints.tsv", base.CAMPAIGN_COLUMNS, rows,
        )

    def apply(self, sha, number, rows=None, timeout=2, limit=16):
        if rows is None:
            rows = []
        self.report(rows, sha)
        token = f"{number}.1"
        run_url = RUN + str(number)
        base.triage(self.campaign, self.history, self.triage_out,
                    sha, token, run_url)
        return check.recheck(
            self.history, self.campaign, self.triage_out,
            self.recheck_out, self.oracle, sha, token, run_url,
            limit=limit, timeout=timeout,
        )

    def status(self):
        records = base.read_tsv(
            self.history / "rechecks.tsv", check.STATUS_COLUMNS,
        )
        return records[0]

    def events(self):
        return base.read_tsv(self.recheck_out / "events.tsv", check.EVENT_COLUMNS)

    def first(self):
        row = self.row()
        result = self.apply(SHA1, 123, [row])
        self.assertEqual(result["archived"], 1)
        self.assertEqual(result["checked"], 0)
        self.assertEqual(self.events(), [])
        signature_id = base.fingerprint_id(row)
        path = self.history / "witnesses" / f"{signature_id}.ir"
        self.assertEqual(path.read_text(), "fixture minimized.ir\n")
        self.assertEqual(self.status()["state"], "reproduces")
        self.assertEqual(self.status()["id"], signature_id)
        return path

    def test_saved_minimized_witness_and_true_replay_transitions(self):
        self.first()
        self.behavior.write_text("agreement")
        self.assertEqual(self.apply(SHA2, 124)["suspected"], 1)
        self.assertEqual(self.status()["state"], "not-reproduced")
        self.assertEqual(self.status()["agree_revisions"], "1")
        self.assertEqual([x["event"] for x in self.events()], ["suspected-fix"])

        self.assertEqual(self.apply(SHA3, 125)["suspected"], 1)
        self.assertEqual(self.status()["agree_revisions"], "2")
        self.assertEqual([x["event"] for x in self.events()],
                         ["second-revision-agreement"])
        self.assertEqual(self.apply(SHA3, 126)["suspected"], 1)
        self.assertEqual(self.status()["agree_revisions"], "2")
        self.assertEqual(self.events(), [])

        self.behavior.write_text("reproduces")
        self.apply(SHA4, 127)
        self.assertEqual(self.status()["state"], "reproduces")
        self.assertEqual(self.status()["agree_revisions"], "0")
        self.assertEqual([x["event"] for x in self.events()], ["regression"])
        self.assertEqual(self.apply(SHA4, 127)["transitions"], 0)
        self.assertEqual(self.events(), [])

    def test_clean_rotating_campaign_never_implies_fix(self):
        self.first()
        self.behavior.write_text("reproduces")
        self.assertEqual(self.apply(SHA2, 124)["suspected"], 0)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.status()["state"], "reproduces")

    def test_changed_wrong_value_and_reference_shift_are_not_fixes(self):
        self.first()
        self.behavior.write_text("changed")
        self.apply(SHA2, 124)
        self.assertEqual(self.status()["state"], "other-wrong-value")
        self.assertEqual(self.status()["agree_revisions"], "0")
        self.assertEqual([x["event"] for x in self.events()],
                         ["changed-wrong-value"])
        self.behavior.write_text("reference-shift")
        self.apply(SHA3, 125)
        self.assertEqual(self.status()["state"], "unavailable")
        self.assertEqual(self.events(), [])

    def test_oracle_error_missing_ir_and_timeout_fail_closed(self):
        witness = self.first()
        self.behavior.write_text("error")
        self.apply(SHA2, 124)
        self.assertEqual(self.status()["state"], "unavailable")
        self.assertEqual(self.events(), [])
        witness.unlink()
        self.apply(SHA3, 125)
        self.assertEqual(self.status()["state"], "unavailable")
        self.assertEqual(self.events(), [])
        witness.write_text("restored witness")
        self.behavior.write_text("timeout")
        self.apply(SHA4, 126, timeout=1)
        self.assertEqual(self.status()["state"], "unavailable")
        self.assertEqual(self.events(), [])

    def test_bad_initial_report_or_invalid_run_cannot_archive(self):
        row = self.row()
        self.report([row], SHA1)
        self.triage_out.mkdir()
        base.write_tsv(self.triage_out / "new.tsv", base.NEW_COLUMNS,
                       [{"id": base.fingerprint_id(row),
                         **{k: row[k] for k in base.KEY},
                         "occurrences": "2", "first_profile": "linear-pure",
                         "first_seed": "1", "issue_body": "issue.md"}])
        with self.assertRaisesRegex(ValueError, "triage must validate"):
            check.recheck(
                self.history, self.campaign, self.triage_out,
                self.recheck_out, self.oracle, SHA1, "123.1", RUN + "123",
            )
        self.assertFalse((self.history / "witnesses").exists())

    def test_invalid_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsafe recheck budgets"):
            check.recheck(self.history, self.campaign, self.triage_out,
                          self.recheck_out, self.oracle, SHA1, "123.1",
                          RUN + "123", limit=0)


if __name__ == "__main__":
    unittest.main()
