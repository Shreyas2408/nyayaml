"""Citation verification — validates cited sections exist and are relevant.

A citation ID identifies one section of one ingested act::

    BNS-2023-ff92dcc72778-S103
    └┬┘ └┬─┘ └────┬─────┘ └┬─┘
     │   │        │        └── section within the act ("S" + section number)
     │   │        └─────────── corpus build hash (12 lowercase hex chars)
     │   └──────────────────── enactment year
     └──────────────────────── act (BNS, BNSS, BSA)

Verification is syntactic (the ID is well formed) and semantic (the ID exists
in the corpus the caller supplies). A verifier never guesses: an ID that is
absent from the corpus is flagged as unresolved rather than silently accepted.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable

CitationId = str

_ACTS = "BNS|BNSS|BSA"
CITATION_PATTERN = re.compile(rf"^(?:{_ACTS})-\d{{4}}-[0-9a-f]{{12}}-S[0-9A-Za-z]+$")

MALFORMED = "malformed"
NOT_FOUND = "not_found"


class UnresolvedCitationError(Exception):
    """Raised when a citation cannot be resolved against the corpus."""

    def __init__(self, citation_id: CitationId, reason: str) -> None:
        self.citation_id = citation_id
        self.reason = reason
        super().__init__(f"Unresolved citation {citation_id!r} ({reason})")


@dataclass(frozen=True)
class CitationCheck:
    """Outcome of verifying a single citation ID."""

    citation_id: CitationId
    exists: bool
    reason: str | None = None


@dataclass(frozen=True)
class CitationReport:
    """Outcome of verifying a batch of citation IDs."""

    verified_ids: list[CitationId] = field(default_factory=list)
    unresolved_ids: list[CitationId] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True when every checked citation resolved to a corpus entry."""
        return not self.unresolved_ids


class CitationVerifier:
    """Verifies that cited legal sections exist in the corpus.

    ``known_citation_ids`` is the set of IDs the corpus exposes; it accepts any
    iterable of strings. Keeping the corpus behind this parameter lets the
    verifier be used against a fixture in tests and a database-backed lookup
    in production without changing the interface.
    """

    def __init__(self, known_citation_ids: Iterable[CitationId]) -> None:
        self._known = set(known_citation_ids)

    def verify(self, citation_id: CitationId) -> CitationCheck:
        """Check a single citation ID for syntax and corpus membership."""
        if not isinstance(citation_id, str) or not CITATION_PATTERN.match(citation_id):
            return CitationCheck(citation_id, exists=False, reason=MALFORMED)
        if citation_id not in self._known:
            return CitationCheck(citation_id, exists=False, reason=NOT_FOUND)
        return CitationCheck(citation_id, exists=True)

    def verify_many(self, citation_ids: Iterable[CitationId]) -> CitationReport:
        """Verify a batch, de-duplicating while preserving first-seen order."""
        verified: list[CitationId] = []
        unresolved: list[CitationId] = []
        seen: set[CitationId] = set()

        for citation_id in citation_ids:
            if citation_id in seen:
                continue
            seen.add(citation_id)
            check = self.verify(citation_id)
            if check.exists:
                verified.append(citation_id)
            else:
                unresolved.append(citation_id)

        return CitationReport(verified_ids=verified, unresolved_ids=unresolved)

    def verify_or_raise(self, citation_id: CitationId) -> CitationCheck:
        """Return the check for a resolvable citation, else raise."""
        check = self.verify(citation_id)
        if not check.exists:
            raise UnresolvedCitationError(citation_id, check.reason or NOT_FOUND)
        return check