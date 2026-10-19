"""Hallucination detection — identifies fabricated information.

An LLM answer is only trustworthy if every section it cites was actually part
of the context the model was given. This detector compares the citation IDs in
a response against the IDs carried by the retrieved spans (the chunks returned
by hybrid retrieval) and separates them into grounded and hallucinated sets.

Spans may be mappings (``{"citation_id": ...}``) or objects exposing a
``citation_id`` attribute. Spans without that field ground nothing.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

CitationId = str


@dataclass(frozen=True)
class HallucinationReport:
    """Result of checking cited IDs against retrieved context."""

    cited_ids: list[CitationId] = field(default_factory=list)
    grounded_ids: list[CitationId] = field(default_factory=list)
    hallucinated_ids: list[CitationId] = field(default_factory=list)

    @property
    def is_grounded(self) -> bool:
        """True when no cited ID is missing from the retrieved context."""
        return not self.hallucinated_ids

    @property
    def grounding_ratio(self) -> float:
        """Fraction of cited IDs that were present in the retrieved context.

        A response with no citations has nothing ungrounded, so it scores 1.0.
        """
        if not self.cited_ids:
            return 1.0
        return len(self.grounded_ids) / len(self.cited_ids)


class HallucinationDetector:
    """Detects citations that are absent from the retrieved context."""

    def detect(
        self,
        cited_ids: Iterable[CitationId],
        retrieved_spans: Iterable[Mapping[str, Any] | Any],
    ) -> HallucinationReport:
        """Compare the response's citations with those of the retrieved spans."""
        unique_cited: list[CitationId] = []
        seen: set[CitationId] = set()
        for citation_id in cited_ids:
            if citation_id not in seen:
                seen.add(citation_id)
                unique_cited.append(citation_id)

        retrieved = {
            span_id
            for span in retrieved_spans
            if (span_id := self._citation_id_of(span)) is not None
        }

        grounded = [cid for cid in unique_cited if cid in retrieved]
        hallucinated = [cid for cid in unique_cited if cid not in retrieved]

        return HallucinationReport(
            cited_ids=unique_cited,
            grounded_ids=grounded,
            hallucinated_ids=hallucinated,
        )

    def is_grounded(
        self,
        cited_ids: Iterable[CitationId],
        retrieved_spans: Iterable[Mapping[str, Any] | Any],
    ) -> bool:
        """Convenience predicate for callers that only need a boolean."""
        return self.detect(cited_ids, retrieved_spans).is_grounded

    @staticmethod
    def _citation_id_of(span: Mapping[str, Any] | Any) -> CitationId | None:
        """Read the citation ID from a mapping or an object, if present."""
        if isinstance(span, Mapping):
            return span.get("citation_id")
        return getattr(span, "citation_id", None)