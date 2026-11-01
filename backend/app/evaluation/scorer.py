"""Aggregate per-question retrieval/grounding proxies without I/O.

Missing measurements stay null. The runner assigns zero overlap when retrieval
fails, so failures cannot improve the recall gate by disappearing from its mean.
Latency covers attempted dataset questions, including failures, but not probes.
The five separate refusal probes are a run-level diagnostic shown alongside the
validated block only; they have no candidate, category, or provenance labels.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from statistics import fmean
from typing import Any

from .metrics import percentile

K_VALUES = (1, 3, 5, 10)
DISCLAIMERS = [
    "These metrics are retrieval/grounding proxies computed as citation overlap against gold labels; they are NOT measures of legal correctness or legal advice quality.",
    "The corpus is the ingested 2023 statutory snapshot (BNS/BNSS/BSA as enacted); it may not reflect current law or subsequent amendments.",
    "Rows with needs_legal_review=true are generated candidates pending legal review; their scores are reported separately and must not be quoted as validated performance.",
]


def _mean(values: Iterable[float | None]) -> float | None:
    samples = [value for value in values if value is not None]
    if any(not math.isfinite(value) for value in samples):
        raise ValueError("Metric measurements must be finite")
    return fmean(samples) if samples else None


def aggregate_metrics(rows: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    """Macro-average each available per-question value, retaining empty groups."""
    latencies = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
    answered = [row for row in rows if row.get("answer") is not None]
    hallucination_rate = None
    if mode == "with-llm" and answered:
        flagged = sum(
            bool(row.get("hallucination_flags")) or row.get("citations_valid") is False
            for row in answered
        )
        hallucination_rate = 100.0 * flagged / len(answered)
    return {
        "precision_at_k": {
            str(k): _mean((row.get("precision_at_k") or {}).get(str(k)) for row in rows)
            for k in K_VALUES
        },
        "recall_at_k": {
            str(k): _mean((row.get("recall_at_k") or {}).get(str(k)) for row in rows)
            for k in K_VALUES
        },
        "hallucination_rate_pct": hallucination_rate,
        "refusal_accuracy": None,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "mean": _mean(latencies),
        },
    }


def build_report(
    results: list[dict[str, Any]],
    *,
    mode: str,
    generated_at: str,
    questions_total: int,
    manifest: dict[str, Any],
    filters: dict[str, Any],
    refusal_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the P34-03 report with separate validated and candidate cohorts.

    Each result carries id/category/provenance/needs_legal_review, metric maps
    keyed by str(k), latency_ms, and optional answer/flags/error. Probes carry
    id/answer/error. Failed probes never earn refusal credit. An incomplete probe
    run yields null, rather than a rate over a selectively reduced denominator.
    """
    if mode not in ("retrieval-only", "with-llm"):
        raise ValueError("Unknown evaluation mode")
    if any(type(row["needs_legal_review"]) is not bool for row in results):
        raise ValueError("Each result must have a boolean needs_legal_review")
    validated = [row for row in results if not row["needs_legal_review"]]
    candidates = [row for row in results if row["needs_legal_review"]]
    metrics = aggregate_metrics(validated, mode=mode)
    candidate_metrics = aggregate_metrics(candidates, mode=mode)
    probes = refusal_results or []
    if mode == "with-llm" and validated and len(probes) == 5:
        metrics["refusal_accuracy"] = sum(
            "answer" in probe and probe["answer"] is None and probe.get("error") is None
            for probe in probes
        ) / 5

    def breakdown(field: str) -> dict[str, Any]:
        return {
            value: aggregate_metrics([row for row in results if row[field] == value], mode=mode)
            for value in sorted({row[field] for row in results})
        }

    failures = [
        {"id": row["id"], "error": row["error"]}
        for row in results + probes
        if row.get("error") is not None
    ]
    return {
        "report_version": "p34-03",
        "generated_at": generated_at,
        "mode": mode,
        "dataset": {
            "path": "data/benchmarks/questions_v2.json",
            "version": "v2",
            "questions_total": questions_total,
            "questions_run": len(results),
            "questions_failed": sum(row.get("error") is not None for row in results),
            "validated_rows": len(validated),
            "candidate_rows": len(candidates),
        },
        "manifest": {key: manifest[key] for key in ("corpus_sha256", "chunks_sha256", "citations_sha256")},
        "config": {"top_k": 10, "k_values": list(K_VALUES), "filters": dict(filters)},
        "metrics": metrics,
        "candidate_metrics": candidate_metrics,
        "breakdowns": {
            "by_category": breakdown("category"),
            "by_provenance": breakdown("provenance"),
            "by_needs_legal_review": {"false": metrics, "true": candidate_metrics},
        },
        "failures": failures,
        "disclaimers": list(DISCLAIMERS),
    }
