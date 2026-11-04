"""Read-only host release gate; run with the host venv and existing retrieval artifacts."""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.request import Request, urlopen

RELEASE_GATE_MIN_RECALL_AT_5 = 0.807  # 0.857 validated baseline - 0.05 margin
# Validated rows only; candidate_metrics never determines the recall gate.
ROOT = Path(__file__).resolve().parents[1]


def _disclaimers() -> list[str]:
    """Read the authoritative literals without importing the application dependency graph."""
    source = ROOT / "backend/app/evaluation/scorer.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DISCLAIMERS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Evaluation disclaimers are missing")


def build_runner_args(report_path: str | Path) -> list[str]:
    """Use the evaluator's validated-only threshold and strict failure semantics."""
    return [
        sys.executable, "-m", "backend.app.evaluation.runner",
        "--mode", "retrieval-only",
        "--min-recall-at-5", str(RELEASE_GATE_MIN_RECALL_AT_5),
        "--strict", "--out", str(report_path),
    ]


def _check_qdrant() -> None:
    # Match DenseRetriever's host URL and optional authentication exactly.
    url = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
    headers = {}
    if os.environ.get("QDRANT_API_KEY"):
        headers["api-key"] = os.environ["QDRANT_API_KEY"]
    with urlopen(Request(url + "/collections", headers=headers), timeout=3) as response:
        if response.status != 200:
            raise OSError("Qdrant unavailable")


def main(
    argv: list[str] | None = None,
    *,
    runner_command: Sequence[str] | None = None,
    report_path: str | Path | None = None,
    run_command: Callable = subprocess.run,
    environment_check: Callable[[], None] = _check_qdrant,
) -> int:
    """Return 0 for pass, 1 for regression, 2 for an unusable environment/report.

    Injection points let tests write synthetic JSON without running retrieval.
    Existing reports are rejected so a stale successful report cannot pass a run.
    Runner output is captured rather than echoed: exceptions can contain credentials.
    """
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--out", type=Path)
        args = parser.parse_args(argv)
        path = Path(report_path or args.out or (
            Path(tempfile.mkdtemp(prefix="nyayaml-release-gate-")) / "retrieval.json"
        )).resolve()
        if path.exists() or path.suffix.lower() != ".json":
            print("GATE FAIL: report must be a new JSON path; environment, not regression.")
            return 2
        try:
            environment_check()
        except Exception:
            print("GATE FAIL: Qdrant unreachable; environment, not regression.")
            return 2
        command = list(runner_command) if runner_command is not None else build_runner_args(path)
        result = run_command(
            command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=1200,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
        # Independently read the completed report, even when the evaluator failed.
        report = json.loads(path.read_text(encoding="utf-8"))
        recall = report["metrics"]["recall_at_k"]["5"]
        counts = report["dataset"]
        valid_recall = type(recall) in (float, int) and math.isfinite(recall) and 0 <= recall <= 1
        if not valid_recall or report["mode"] != "retrieval-only" or counts["validated_rows"] <= 0:
            print("GATE FAIL: no valid retrieval-only validated recall@5; environment, not regression.")
            return 2
        print(f"Validated citation-overlap recall@5 = {recall:.3f}")
        print(f"Report: {path}")
        if report["failures"] != [] or counts["questions_failed"] != 0:
            print("GATE FAIL: evaluation failures; environment, not regression (runner --strict).")
            return 2
        if result.returncode not in (0, 1):
            print("GATE FAIL: runner environment error; environment, not regression.")
            return 2
        if recall < RELEASE_GATE_MIN_RECALL_AT_5 or result.returncode == 1:
            print("GATE FAIL: validated recall threshold regression.")
            return 1
        print("GATE PASS")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception:
        print("GATE FAIL: runner or report unavailable/invalid; environment, not regression.")
        return 2
    finally:
        for disclaimer in _disclaimers():
            print(disclaimer)


if __name__ == "__main__":
    sys.exit(main())
