"""Offline gate contract tests; synthetic reports only, no retrieval or services."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock

from backend.app.evaluation.scorer import DISCLAIMERS
from scripts import release_gate as gate


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "report.json"
        self.output = io.StringIO()
        self.check_environment = Mock()

    def run_gate(self, recall, *, returncode=0, failures=None, **overrides):
        report = {
            "mode": "retrieval-only",
            "dataset": {"validated_rows": 21, "questions_failed": 0},
            "metrics": {"recall_at_k": {"5": recall}},
            "candidate_metrics": {"recall_at_k": {"5": 0.0}},
            "failures": [] if failures is None else failures,
        }
        report.update(overrides)

        def fake_runner(command, **kwargs):
            self.path.write_text(json.dumps(report), encoding="utf-8")
            return subprocess.CompletedProcess(command, returncode)

        self.run_command = Mock(side_effect=fake_runner)
        with redirect_stdout(self.output):
            return gate.main(
                [], runner_command=["fake-runner"], report_path=self.path,
                run_command=self.run_command, environment_check=self.check_environment,
            )

    def test_threshold_is_locked(self):
        self.assertEqual(gate.RELEASE_GATE_MIN_RECALL_AT_5, 807 / 1000)

    def test_runner_cli_uses_retrieval_only_strict_and_locked_threshold(self):
        args = gate.build_runner_args(self.path)
        self.assertEqual(args[args.index("--mode") + 1], "retrieval-only")
        index = args.index("--min-recall-at-5")
        self.assertEqual(args[index:index + 2], ["--min-recall-at-5", str(807 / 1000)])
        self.assertIn("--strict", args)
        self.assertEqual(args[args.index("--out") + 1], str(self.path))

    def test_independent_report_check_rejects_low_recall_despite_runner_success(self):
        self.assertEqual(self.run_gate(0.80), 1)
        self.assertIn("GATE FAIL", self.output.getvalue())
        self.assertIn("0.800", self.output.getvalue())

    def test_validated_recall_passes_even_when_candidates_score_zero(self):
        self.assertEqual(self.run_gate(0.86), 0)
        self.assertIn("GATE PASS", self.output.getvalue())
        self.assertIn("0.860", self.output.getvalue())
        self.assertEqual(self.output.getvalue().splitlines()[-3:], DISCLAIMERS)

    def test_threshold_boundary_passes(self):
        self.assertEqual(self.run_gate(gate.RELEASE_GATE_MIN_RECALL_AT_5), 0)

    def test_candidates_cannot_rescue_low_validated_recall(self):
        self.assertEqual(self.run_gate(0.80, candidate_metrics={"recall_at_k": {"5": 1.0}}), 1)

    def test_failures_are_rejected_even_when_runner_reports_success(self):
        self.assertEqual(self.run_gate(0.86, failures=[{"id": "q1", "error": "unavailable"}]), 2)

    def test_nonzero_question_failure_count_is_rejected(self):
        self.assertEqual(self.run_gate(0.86, dataset={"validated_rows": 21, "questions_failed": 1}), 2)

    def test_runner_environment_error_cannot_pass(self):
        self.assertEqual(self.run_gate(0.86, returncode=2), 2)

    def test_runner_regression_cannot_pass(self):
        self.assertEqual(self.run_gate(0.86, returncode=1), 1)

    def test_unavailable_qdrant_exits_without_running_pipeline(self):
        self.check_environment.side_effect = OSError("secret must never be echoed")
        self.assertEqual(self.run_gate(0.86), 2)
        self.run_command.assert_not_called()
        self.assertIn("Qdrant unreachable; environment, not regression", self.output.getvalue())
        self.assertNotIn("secret must", self.output.getvalue())
        self.assertEqual(self.output.getvalue().splitlines()[-3:], DISCLAIMERS)

    def test_missing_or_nonfinite_recall_is_environment_error(self):
        for recall in (None, float("nan"), float("inf"), True):
            with self.subTest(recall=recall):
                self.path.unlink(missing_ok=True)
                self.assertEqual(self.run_gate(recall), 2)

    def test_wrong_mode_or_missing_validated_cohort_is_rejected(self):
        self.assertEqual(self.run_gate(0.86, mode="with-llm"), 2)
        self.path.unlink()
        self.assertEqual(self.run_gate(0.86, dataset={"validated_rows": 0, "questions_failed": 0}), 2)

    def test_stale_report_is_never_used(self):
        self.path.write_text("{}", encoding="utf-8")
        self.assertEqual(self.run_gate(0.86), 2)
        self.run_command.assert_not_called()

    def test_success_without_report_is_environment_error(self):
        with redirect_stdout(self.output):
            result = gate.main(
                [], runner_command=["fake-runner"], report_path=self.path,
                run_command=Mock(return_value=subprocess.CompletedProcess([], 0)),
                environment_check=self.check_environment,
            )
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
