"""Aggregation tests with fabricated per-question measurements only."""

import copy
import unittest

from backend.app.evaluation.scorer import DISCLAIMERS, K_VALUES, build_report

MANIFEST = {key: "a" * 64 for key in ("corpus_sha256", "chunks_sha256", "citations_sha256")}


def result(identifier, *, precision=1.0, recall=1.0, candidate=False, **fields):
    return {
        "id": identifier,
        "category": "direct_lookup",
        "provenance": "generated_candidate" if candidate else "corpus_section",
        "needs_legal_review": candidate,
        "precision_at_k": {str(k): precision for k in K_VALUES},
        "recall_at_k": {str(k): recall for k in K_VALUES},
        "latency_ms": 10.0,
        **fields,
    }


def report(rows, **kwargs):
    return build_report(
        rows, generated_at="2026-11-01T00:00:00Z", questions_total=32,
        manifest=MANIFEST, filters={}, **{"mode": "retrieval-only", **kwargs},
    )


class ScorerTests(unittest.TestCase):
    def test_macro_averages_and_all_three_breakdowns_keep_candidates_separate(self):
        # One hit out of one gold and one out of four: macro .625, micro .4.
        rows = [
            result("a", recall=1.0),
            result("b", recall=0.25, category="procedural", provenance="gold_retrieval"),
            result("c", precision=0.0, recall=0.0, candidate=True),
        ]
        out = report(rows)
        self.assertEqual(out["metrics"]["recall_at_k"]["5"], 0.625)
        self.assertEqual(out["metrics"]["precision_at_k"]["1"], 1.0)
        self.assertEqual(out["candidate_metrics"]["recall_at_k"]["5"], 0.0)
        groups = out["breakdowns"]
        self.assertEqual(groups["by_category"]["direct_lookup"]["recall_at_k"]["5"], 0.5)
        self.assertEqual(groups["by_provenance"]["gold_retrieval"]["recall_at_k"]["5"], 0.25)
        self.assertEqual(groups["by_needs_legal_review"]["false"], out["metrics"])
        self.assertEqual(groups["by_needs_legal_review"]["true"], out["candidate_metrics"])
        self.assertEqual(out["dataset"]["validated_rows"], 2)
        self.assertEqual(out["dataset"]["candidate_rows"], 1)

    def test_latency_uses_r7_on_unsorted_wall_times_without_mutating_results(self):
        rows = [result(str(i), latency_ms=value) for i, value in enumerate([40.0, 10.0, 30.0, 20.0])]
        before = copy.deepcopy(rows)
        latency = report(rows)["metrics"]["latency_ms"]
        self.assertEqual(latency["p50"], 25.0)
        self.assertEqual(latency["p95"], 38.5)
        self.assertAlmostEqual(latency["p99"], 39.7, places=12)
        self.assertEqual(latency["mean"], 25.0)
        self.assertEqual(rows, before)

    def test_hallucination_proxy_counts_answered_questions_once(self):
        rows = [
            result("a", answer="text", hallucination_flags=["bad"], citations_valid=False),
            result("b", answer="", hallucination_flags=[], citations_valid=False),
            result("c", answer="text", hallucination_flags=[], citations_valid=True),
            result("d", answer=None, hallucination_flags=["bad"], citations_valid=False),
            result("e", candidate=True, answer="text", hallucination_flags=["bad"]),
        ]
        out = report(rows, mode="with-llm")
        self.assertAlmostEqual(out["metrics"]["hallucination_rate_pct"], 200 / 3)
        self.assertEqual(out["candidate_metrics"]["hallucination_rate_pct"], 100.0)
        self.assertIsNone(report(rows)["metrics"]["hallucination_rate_pct"])

    def test_absent_measurements_and_empty_cohorts_stay_null(self):
        for rows in ([], [result("missing", precision_at_k=None, recall_at_k={}, latency_ms=None)]):
            with self.subTest(rows=rows):
                out = report(rows, mode="with-llm")
                for name in ("metrics", "candidate_metrics"):
                    block = out[name]
                    self.assertEqual(block["precision_at_k"], {str(k): None for k in K_VALUES})
                    self.assertEqual(block["recall_at_k"], {str(k): None for k in K_VALUES})
                    self.assertEqual(block["latency_ms"], dict.fromkeys(("p50", "p95", "p99", "mean")))
                    self.assertIsNone(block["hallucination_rate_pct"])
                    self.assertIsNone(block["refusal_accuracy"])

    def test_refusal_probes_use_fixed_denominator_and_errors_do_not_earn_credit(self):
        probes = [
            {"id": "probe-1", "answer": None},
            {"id": "probe-2", "answer": None},
            {"id": "probe-3", "answer": "out-of-scope answer"},
            {"id": "probe-4", "answer": None, "error": "llm_unavailable"},
            {"id": "probe-5", "answer": ""},
        ]
        rows = [result("validated"), result("candidate", candidate=True)]
        out = report(rows, mode="with-llm", refusal_results=probes)
        self.assertEqual(out["metrics"]["refusal_accuracy"], 0.4)
        self.assertIsNone(out["candidate_metrics"]["refusal_accuracy"])
        self.assertEqual(out["dataset"]["questions_run"], 2)
        self.assertEqual(out["dataset"]["questions_failed"], 0)
        self.assertEqual(out["failures"], [{"id": "probe-4", "error": "llm_unavailable"}])
        self.assertIsNone(report(rows, refusal_results=probes)["metrics"]["refusal_accuracy"])
        self.assertIsNone(report(rows, mode="with-llm", refusal_results=probes[:4])["metrics"]["refusal_accuracy"])
        self.assertIsNone(report(rows[1:], mode="with-llm", refusal_results=probes)["metrics"]["refusal_accuracy"])

    def test_exact_report_keys_and_question_failure_accounting(self):
        out = report([result("failed", precision=0, recall=0, error="offline"), result("ok")])
        self.assertEqual(set(out), {
            "report_version", "generated_at", "mode", "dataset", "manifest", "config",
            "metrics", "candidate_metrics", "breakdowns", "failures", "disclaimers",
        })
        self.assertEqual(set(out["dataset"]), {
            "path", "version", "questions_total", "questions_run", "questions_failed",
            "validated_rows", "candidate_rows",
        })
        self.assertEqual(set(out["metrics"]), {
            "precision_at_k", "recall_at_k", "hallucination_rate_pct", "refusal_accuracy", "latency_ms",
        })
        self.assertEqual(out["dataset"]["questions_failed"], 1)
        self.assertEqual(out["metrics"]["recall_at_k"]["5"], 0.5)
        self.assertEqual(out["failures"], [{"id": "failed", "error": "offline"}])
        self.assertEqual(out["disclaimers"], DISCLAIMERS)
        self.assertEqual(out["manifest"], MANIFEST)


if __name__ == "__main__":
    unittest.main()
