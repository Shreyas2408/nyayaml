"""DB-free summaries of the caller's audit measurements."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import fmean

from app.evaluation.metrics import percentile
from app.persistence.repository import UsageRow


def compute_summary(rows: list[UsageRow]) -> dict:
    """Summarize all rows; latency statistics exclude nulls, but include zeros.

    The last-24-hour window is inclusive and ends at the current UTC time.
    This function performs no database I/O and never mutates its input.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    timestamps = [row.created_at for row in rows]
    return {
        "total_queries": len(rows),
        "by_status": dict(sorted(Counter(row.status for row in rows).items())),
        "queries_last_24h": sum(cutoff <= timestamp <= now for timestamp in timestamps),
        "avg_latency_ms": fmean(latencies) if latencies else None,
        "p50_latency_ms": percentile(latencies, 50),
        "first_query_at": min(timestamps).isoformat() if timestamps else None,
        "last_query_at": max(timestamps).isoformat() if timestamps else None,
    }
