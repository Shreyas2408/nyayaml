# from __future__ import annotations

# import hashlib
# import re
# import unicodedata
# import uuid
# from bisect import bisect_right
# from dataclasses import dataclass, field
# from enum import Enum
# from pathlib import Path
# from typing import Iterator, Sequence

# # ----- Constants -----
# SCHEMA_VERSION = "1.0.0"

# BGE_MAX_TOKENS= 512
# CHUNK_TOKEN_BUDGET= 480

# QDRANT_ID_NAMESPACE= uuid.UUID("6f1e5a2c-8b3d-4f7a-9c1e-0d2b4a6c8e10")




# class SchemaViolation(ValueError):
#     """ A record violated a non negotiable structural invariant. 
#     rasied at construction time, callers in the pipeline catch this. convert it to ''PraseDefect'' with 
#     ''DefectSeverity.BLOCKING'' , and contuie parsing so one bad section does not abort a Section /act."""

# #utilities

# _WS_RE = re.compile(r"\s+")

# def normalize_ws(text: str) -> str:
#     """NFKC-normalise, collapse alll whitespace runs,strip. comparison only"""
#     return _WS_RE.sub(" ",unicodedata.normalize("NFKC", text)).strip()

# def sha256_bytes(payload: bytes) -> str:
#     return hashlib.sha256(payload).hexadigest()

# def sha256_file(path: str | Path) -> str:
#     digest=hashlib.sha256()
#     with open(path, "rb") as handle:
#         for block in iter(lambda: handle.read(1<<20), b""):
#             digest.update(block)
#     return digest.hexdigest()

# def estimate_token(text: str) -> int:
#     """ Cheap upper bound token estimate(~4 char/token) for planning only.
#     STEp 8 replaces this with real BGE tokenizer, its just for the logs"""
#     return max(1, (len(text)+3) // 4)

# # ------ marker run validation(N+1 invariant)

# _NUMERIC_MAKER_RE = re.compile(r"^\((\d{1,3})([A-Za-z]{0,2})\)$")
# _ALPHA_MARKER_RE= re.compile(r"^\(([a-z]{1,4})\)$")
# _ROMAN_CHARS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


# def _roman_to_int(token: str) -> int| None:
#     if not token or any(ch in _ROMAN_CHARS for ch in token):
#         return None
#     total,highest= 0,0
#     for ch in reversed(token):
#         value = _ROMAN_CHARS[ch]
#         total += -value if value < highest else value
#         highest = max(highest,value)
#     return total or None

# def _alpha_to_int(token:str) ->int | None:
#     if not token or not token.isalpha():
#         return None
#     total = 0
#     for ch in token:
#         total = total*26 + (ord(ch) -96)
#     return total

# def _numeric_run_ok(markers: Sequence[str]) -> bool:
#     parsed: List[tuple[int.str]] = []  #so after ':' is type hint ie the parsed is a empty list whch be have like (1, "marker_name")
#     for marker in markers:
#         match = _NUMERIC_MAKER_RE.match(marker)
#         if match is None:
#             return False
#         parsed.append((int(match.group(1)), match.group(2).upper()))
#     if len(set(parsed))!= len(parsed):
#         return False
#     numbers = [number for number,_ in parsed]
#     return _is_dense_run(numbers)

# def _is_dense_run(ordinals: List[int]) -> bool:
#     if not ordinals:
#         return False
#     return (
#         ordinals == sorted(ordinals)
#         and sorted(set(ordinals)) == list(range(1, max(ordinals)+1))
#     )

# def _symbolic_run_ok(markers: Sequence[str], to_int) -> bool:
#     ordinals: List[int] = []
#     for marker in markers:
#         match = _ALPHA_MARKER_RE.match(marker)
#         if match is None:
#             return False
#         value = to_int(match.group(1))
#         if value is None:
#             return False
#         ordinals.append(value)
#     return len(set(ordinals)) ==len(ordinals) and _is_dense_run(ordinals)

# def check_marker_sequence( markers: Sequence[str|None],label:str, where:str)->NOne:
#     """ it applies N+1 invarient/ie rule for a string of sibling markers.
#         most imp production gate, as our old output had flaw ascross-reference -- "sub-sections (1) and
#     (2) of section 132" -- brought some structural nodes. this alsways results in 
#     as a non-monotonic or duplicated sibling run, e.g. BNSS S.14's
#     ``['(1)','(2)','(3)','(4)','(5)','(4)','(6)')``.
#     now it acceppts ``(1) (1A) (2)``, roman ``(i) (ii) (iii)``, or alphabetic
#     ``(a) (b) (c)`` runs -- whichever interpretation yields a dense 1..N
#     sequence. Unlabelled nodes are legal only if *every* sibling is unlabelled
#     (a flat section with a single implicit body).
#     """
#     labelled= [m for m in marekers if m]
#     if not labelled:
#         return
#     if len(labelled)!= len(markers):
#         raise SchemaViolation(
#             f"{where}:{label} run mixes labelled and unlabelled siblings: "
#             f"{list(marekers)}"
#         )

#     if _numeric_run_ok(labelled):
#         return

#     if _symbolic_run_ok(labelled, _roman_to_int):
#         return
#     if _symbolic_run_ok(labelled, _alpha_to_int):
#         return
#     raise SchemaViolation(
#         f"{where}: {label} run is not a dense 1..N sequence: {list(labelled)} "
#         f"-- probable cross-reference promoted to a structural node"
#     )


"""Canonical data models for the NyayaML ingestion pipeline.

Every structure that crosses a stage boundary inside ``pipeline/`` is defined
here exactly once. Downstream modules import from this file and never redefine
a shape.

Design axioms
-------------
1. FAIL CLOSED.  Validation lives in ``__post_init__``, so a corrupt record
   cannot be *constructed* at all.  A violation raises ``SchemaViolation`` and
   is logged as a ``ParseDefect``; it is never coerced into a
   plausible-but-wrong record that could reach the vector store and be cited
   back to a user as law.

2. EVIDENCE FIRST.  Layers 0-2 reason in offsets (``Span``) over one immutable
   character stream.  Text is copied exactly once, at Layer 3, after the
   partition has been proven total.  Nothing is deleted from the stream; it is
   only ever *classified*.

3. VERBATIM CITATION.  ``Section.text`` and ``Chunk.text`` are byte-faithful
   extracts, safe to quote as legal authority.  Synthetic scaffolding lives
   only in ``Chunk.embed_text``, which is fed to the encoder and never shown.

All records are ``frozen=True, slots=True``: hashable, compact, and immutable,
so a later stage cannot rewrite an earlier stage's evidence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"

#: Hard context window of BAAI/bge-base-en-v1.5. Text beyond this is silently
#: truncated by the encoder, which is a data-loss bug that leaves no trace.
BGE_MAX_TOKENS = 512

#: Budget for the verbatim body, leaving headroom for the synthetic context
#: header prepended in ``Chunk.embed_text``.
CHUNK_TOKEN_BUDGET = 480

#: Frozen namespace for deterministic Qdrant point IDs. NEVER CHANGE THIS.
#: Changing it re-keys every vector in every collection and turns idempotent
#: re-ingestion into silent duplication.
QDRANT_ID_NAMESPACE = uuid.UUID("6f1e5a2c-8b3d-4f7a-9c1e-0d2b4a6c8e10")


class SchemaViolation(ValueError):
    """A record violated a non-negotiable structural invariant.

    Raised at construction time. Callers in the pipeline catch this, convert it
    to a ``ParseDefect`` with ``DefectSeverity.BLOCKING``, and continue parsing
    so one bad section does not abort a 531-section act.
    """


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """NFKC-normalise, collapse all whitespace runs, strip. Comparison only.

    Used by coverage assertions so that a reflowed line break never registers
    as missing text. Never write the result back into a citation field.
    """
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def estimate_tokens(text: str) -> int:
    """Cheap upper-bound token estimate (~4 chars/token) for planning only.

    Step 8 replaces this with the real BGE tokenizer before any ``Chunk`` is
    constructed; this exists so earlier stages can log size warnings without
    loading a 400 MB model.
    """
    return max(1, (len(text) + 3) // 4)


# --- marker-run validation (the N+1 invariant) ------------------------------

_NUMERIC_MARKER_RE = re.compile(r"^\((\d{1,3})([A-Za-z]{0,2})\)$")
_ALPHA_MARKER_RE = re.compile(r"^\(([a-z]{1,4})\)$")
_ROMAN_CHARS = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman_to_int(token: str) -> int | None:
    if not token or any(ch not in _ROMAN_CHARS for ch in token):
        return None
    total, highest = 0, 0
    for ch in reversed(token):
        value = _ROMAN_CHARS[ch]
        total += -value if value < highest else value
        highest = max(highest, value)
    return total or None


def _alpha_to_int(token: str) -> int | None:
    """Bijective base-26: a=1 ... z=26, aa=27."""
    if not token or not token.isalpha():
        return None
    total = 0
    for ch in token:
        total = total * 26 + (ord(ch) - 96)
    return total


def _is_dense_run(ordinals: list[int]) -> bool:
    """True iff ``ordinals`` is non-decreasing and covers exactly 1..max."""
    if not ordinals:
        return False
    return (
        ordinals == sorted(ordinals)
        and sorted(set(ordinals)) == list(range(1, max(ordinals) + 1))
    )


def _numeric_run_ok(markers: Sequence[str]) -> bool:
    parsed: list[tuple[int, str]] = []
    for marker in markers:
        match = _NUMERIC_MARKER_RE.match(marker)
        if match is None:
            return False
        parsed.append((int(match.group(1)), match.group(2).upper()))
    if len(set(parsed)) != len(parsed):      # exact duplicate marker
        return False
    numbers = [number for number, _ in parsed]
    return _is_dense_run(numbers)


def _symbolic_run_ok(markers: Sequence[str], to_int) -> bool:
    ordinals: list[int] = []
    for marker in markers:
        match = _ALPHA_MARKER_RE.match(marker)
        if match is None:
            return False
        value = to_int(match.group(1))
        if value is None:
            return False
        ordinals.append(value)
    return len(set(ordinals)) == len(ordinals) and _is_dense_run(ordinals)


def check_marker_sequence(
    markers: Sequence[str | None], label: str, where: str
) -> None:
    """Enforce the N+1 invariant across a run of sibling markers.

    This is the single highest-yield gate in the pipeline. The dominant real
    defect in the legacy output is a cross-reference -- "sub-sections (1) and
    (2) of section 132" -- promoted into structural nodes. That always surfaces
    as a non-monotonic or duplicated sibling run, e.g. BNSS S.14's
    ``['(1)','(2)','(3)','(4)','(5)','(4)','(6)')``.

    Accepts numeric ``(1) (1A) (2)``, roman ``(i) (ii) (iii)``, or alphabetic
    ``(a) (b) (c)`` runs -- whichever interpretation yields a dense 1..N
    sequence. Unlabelled nodes are legal only if *every* sibling is unlabelled
    (a flat section with a single implicit body).
    """
    labelled = [m for m in markers if m]
    if not labelled:
        return
    if len(labelled) != len(markers):
        raise SchemaViolation(
            f"{where}: {label} run mixes labelled and unlabelled siblings: "
            f"{list(markers)}"
        )
    if _numeric_run_ok(labelled):
        return
    if _symbolic_run_ok(labelled, _roman_to_int):
        return
    if _symbolic_run_ok(labelled, _alpha_to_int):
        return
    raise SchemaViolation(
        f"{where}: {label} run is not a dense 1..N sequence: {list(labelled)} "
        f"-- probable cross-reference promoted to a structural node"
    )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Act(str, Enum):
    """The three 2023 criminal codes. Values are the citation prefixes."""

    BNS = "BNS"
    BNSS = "BNSS"
    BSA = "BSA"

    @property
    def long_name(self) -> str:
        return _ACT_META[self]["long_name"]

    @property
    def expected_sections(self) -> int:
        """Authoritative section count. A parse that misses is a build failure."""
        return _ACT_META[self]["expected_sections"]

    @property
    def source_pdf(self) -> str:
        return _ACT_META[self]["source_pdf"]

    @property
    def output_json(self) -> str:
        return f"{self.value}_2023_parsed.json"

    @property
    def interim_txt(self) -> str:
        return f"{self.value}_2023_raw.txt"

    @property
    def qdrant_collection(self) -> str:
        return f"nyayaml_{self.value.lower()}"


_ACT_META: dict[Act, dict] = {
    Act.BNS: {
        "long_name": "Bharatiya Nyaya Sanhita, 2023",
        "expected_sections": 358,
        "source_pdf": "Bns_2023.pdf",
    },
    Act.BNSS: {
        "long_name": "Bharatiya Nagarik Suraksha Sanhita, 2023",
        "expected_sections": 531,
        "source_pdf": "Bnss_2023.pdf",
    },
    Act.BSA: {
        "long_name": "Bharatiya Sakshya Adhiniyam, 2023",
        "expected_sections": 170,
        "source_pdf": "Bsa_2023.pdf",
    },
}

#: 358 + 531 + 170. Asserted by Gate A at the end of every full ingestion run.
TOTAL_EXPECTED_SECTIONS = sum(act.expected_sections for act in Act)


class LandmarkKind(str, Enum):
    """Classification assigned to a candidate anchor during Layer 1."""

    ENACTING_FORMULA = "enacting_formula"
    PART = "part"
    CHAPTER = "chapter"
    SECTION_CANDIDATE = "section_candidate"
    TOC_ENTRY = "toc_entry"
    SCHEDULE = "schedule"


class PipelineStage(str, Enum):
    ACQUISITION = "acquisition"
    LANDMARK = "landmark"
    BOUNDARY = "boundary"
    HIERARCHY = "hierarchy"
    METADATA = "metadata"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    LOADING = "loading"


class DefectSeverity(str, Enum):
    #: Blocks embedding. Ships nothing until resolved.
    BLOCKING = "blocking"
    #: Recorded, reviewed, does not block.
    WARNING = "warning"


class RecoveryStrategy(str, Enum):
    """How a section boundary was resolved, for provenance and audit."""

    NONE = "none"                              # clean first-pass match
    PAGE_REJOIN = "R1_page_rejoin"             # header split across a page break
    RELAXED_TERMINATOR = "R2_relaxed_terminator"  # internal period in the title
    TOC_ORACLE = "R3_toc_oracle"               # title recovered from the TOC


# ---------------------------------------------------------------------------
# Offset primitives (Layers 0-2 currency)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """A half-open character interval ``[char_start, char_end)``."""

    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise SchemaViolation(f"Span start is negative: {self.char_start}")
        if self.char_end < self.char_start:
            raise SchemaViolation(
                f"Span is inverted: [{self.char_start}, {self.char_end})"
            )

    @property
    def length(self) -> int:
        return self.char_end - self.char_start

    def contains(self, offset: int) -> bool:
        return self.char_start <= offset < self.char_end

    def overlaps(self, other: "Span") -> bool:
        return self.char_start < other.char_end and other.char_start < self.char_end

    def slice(self, stream: str) -> str:
        return stream[self.char_start : self.char_end]

    def to_list(self) -> list[int]:
        return [self.char_start, self.char_end]


def merge_spans(spans: Sequence[Span]) -> tuple[Span, ...]:
    """Sort and coalesce touching or overlapping intervals."""
    if not spans:
        return ()
    ordered = sorted(spans)
    merged = [ordered[0]]
    for span in ordered[1:]:
        last = merged[-1]
        if span.char_start <= last.char_end:
            merged[-1] = Span(last.char_start, max(last.char_end, span.char_end))
        else:
            merged.append(span)
    return tuple(merged)


def subtract_spans(total: Span, removals: Sequence[Span]) -> tuple[Span, ...]:
    """Return the ordered gaps of ``total`` left after removing ``removals``.

    This is the allocator behind Layer 2's "decide once, allocate last" rule:
    a section body is the remainder of its interval after every classified
    noise span (page numbers, footnotes, welded chapter headings) is carved
    out. Because it is pure interval arithmetic, no character can be dropped
    by accident and none can be double-counted.
    """
    gaps: list[Span] = []
    cursor = total.char_start
    for removal in merge_spans(removals):
        if removal.char_end <= total.char_start or removal.char_start >= total.char_end:
            continue
        start = max(removal.char_start, total.char_start)
        if start > cursor:
            gaps.append(Span(cursor, start))
        cursor = max(cursor, min(removal.char_end, total.char_end))
    if cursor < total.char_end:
        gaps.append(Span(cursor, total.char_end))
    return tuple(gaps)


@dataclass(frozen=True, slots=True)
class PageSpan:
    """One PDF page's footprint in the flattened character stream.

    ``page_no`` is the *printed Gazette* number, which is what a citation must
    reference and which does not equal ``page_index`` (front matter is
    unnumbered). It is ``None`` when the page carries no printed folio.
    """

    page_index: int          # 0-based physical order
    char_start: int
    char_end: int
    page_no: int | None = None

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise SchemaViolation(f"page_index is negative: {self.page_index}")
        Span(self.char_start, self.char_end)   # reuse interval validation

    @property
    def span(self) -> Span:
        return Span(self.char_start, self.char_end)

    def to_dict(self) -> dict:
        return {
            "page_index": self.page_index,
            "page_no": self.page_no,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Layer 0 output: one immutable character stream plus its provenance.

    ``text`` is never mutated downstream. Every later stage refers to regions
    of it by offset, which is what makes the zero-loss coverage assertion
    meaningful: there is exactly one authoritative copy to compare against.
    """

    act: Act
    source_path: str
    source_sha256: str
    extractor: str            # e.g. "pdfplumber==0.11.10"
    extracted_at: str         # ISO-8601 UTC
    text: str
    pages: tuple[PageSpan, ...] = ()
    _page_starts: tuple[int, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.act, Act):
            raise SchemaViolation(f"act must be an Act, got {type(self.act)!r}")
        if len(self.source_sha256) != 64:
            raise SchemaViolation(
                f"source_sha256 must be 64 hex chars, got {len(self.source_sha256)}"
            )
        if not self.text:
            raise SchemaViolation(f"{self.act.value}: extracted text is empty")
        object.__setattr__(self, "pages", tuple(self.pages))
        for index, page in enumerate(self.pages):
            if page.char_end > len(self.text):
                raise SchemaViolation(
                    f"{self.act.value}: page {index} ends at {page.char_end}, "
                    f"past stream length {len(self.text)}"
                )
        object.__setattr__(
            self, "_page_starts", tuple(page.char_start for page in self.pages)
        )

    def __len__(self) -> int:
        return len(self.text)

    @property
    def span(self) -> Span:
        return Span(0, len(self.text))

    @property
    def text_sha256(self) -> str:
        return sha256_bytes(self.text.encode("utf-8"))

    def slice(self, span: Span) -> str:
        return span.slice(self.text)

    def page_of(self, offset: int) -> PageSpan | None:
        """Locate the page containing ``offset`` in O(log n)."""
        if not self._page_starts:
            return None
        index = bisect_right(self._page_starts, offset) - 1
        return self.pages[index] if index >= 0 else None

    def to_manifest(self) -> dict:
        """Provenance record. Excludes ``text``; that goes to the sidecar."""
        return {
            "schema_version": SCHEMA_VERSION,
            "act": self.act.value,
            "long_name": self.act.long_name,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "extractor": self.extractor,
            "extracted_at": self.extracted_at,
            "char_count": len(self.text),
            "page_count": len(self.pages),
            "text_sha256": self.text_sha256,
        }


@dataclass(frozen=True, slots=True)
class Landmark:
    """A classified anchor found in the stream during Layer 1.

    Landmarks are recorded, not consumed: the chapter interval map, the TOC
    oracle, and the boundary state machine all read the same landmark list, so
    they can never disagree about where a heading is.
    """

    kind: LandmarkKind
    char_start: int
    char_end: int
    raw: str
    roman: str | None = None      # CHAPTER / PART
    number: str | None = None     # SECTION_CANDIDATE / TOC_ENTRY
    title: str | None = None
    page_no: int | None = None

    def __post_init__(self) -> None:
        Span(self.char_start, self.char_end)
        if self.kind in (LandmarkKind.CHAPTER, LandmarkKind.PART) and not self.roman:
            raise SchemaViolation(f"{self.kind.value} landmark requires roman: {self.raw!r}")
        if self.kind in (
            LandmarkKind.SECTION_CANDIDATE,
            LandmarkKind.TOC_ENTRY,
        ) and not self.number:
            raise SchemaViolation(f"{self.kind.value} landmark requires number: {self.raw!r}")
        if self.kind is LandmarkKind.TOC_ENTRY and not self.title:
            raise SchemaViolation(f"TOC entry requires a title: {self.raw!r}")

    @property
    def span(self) -> Span:
        return Span(self.char_start, self.char_end)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "roman": self.roman,
            "number": self.number,
            "title": self.title,
            "page_no": self.page_no,
            "raw": self.raw,
        }


_SECTION_NUMBER_RE = re.compile(r"^\d{1,3}[A-Z]{0,3}$")
_ROMAN_ONLY_RE = re.compile(r"^[IVXLCDM]{1,8}$")


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """Layer 2 output: a decided section boundary, still offsets only.

    No text has been copied yet. ``header`` and ``body`` are adjacent and
    disjoint; the union of every ``SectionSpan`` plus every classified noise
    span must exactly tile the stream, which is what Gate E checks.
    """

    act: Act
    section_number: str
    header: Span
    body: Span
    title: str | None = None
    chapter_landmark_index: int | None = None
    recovery: RecoveryStrategy = RecoveryStrategy.NONE
    title_source: str = "body"      # "body" | "toc" | "unresolved"

    def __post_init__(self) -> None:
        if not _SECTION_NUMBER_RE.match(self.section_number):
            raise SchemaViolation(
                f"{self.act.value}: malformed section_number {self.section_number!r}"
            )
        if self.body.char_start < self.header.char_end:
            raise SchemaViolation(
                f"{self.act.value} S.{self.section_number}: body starts at "
                f"{self.body.char_start}, before header ends at {self.header.char_end}"
            )
        if self.title_source not in ("body", "toc", "unresolved"):
            raise SchemaViolation(f"unknown title_source {self.title_source!r}")

    @property
    def full(self) -> Span:
        return Span(self.header.char_start, self.body.char_end)

    @property
    def sort_key(self) -> int:
        return self.header.char_start

    def to_dict(self) -> dict:
        return {
            "act": self.act.value,
            "section_number": self.section_number,
            "title": self.title,
            "header": self.header.to_list(),
            "body": self.body.to_list(),
            "chapter_landmark_index": self.chapter_landmark_index,
            "recovery": self.recovery.value,
            "title_source": self.title_source,
        }


# ---------------------------------------------------------------------------
# Defect ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParseDefect:
    """One recorded failure. The unit of the fail-closed contract.

    A ``BLOCKING`` defect anywhere in an act prevents that act from being
    embedded. This is the mechanism that stops a silently truncated section --
    the BSA S.21 failure mode, which lost 96% of its text while still looking
    structurally valid -- from being served as authoritative law.
    """

    act: Act
    stage: PipelineStage
    severity: DefectSeverity
    code: str                     # stable machine-readable slug
    message: str
    section_number: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    page_no: int | None = None

    def __post_init__(self) -> None:
        if not self.code or " " in self.code:
            raise SchemaViolation(f"defect code must be a slug, got {self.code!r}")

    def __str__(self) -> str:
        locus = f"{self.act.value}"
        if self.section_number:
            locus += f" S.{self.section_number}"
        if self.page_no is not None:
            locus += f" p.{self.page_no}"
        return f"[{self.severity.value.upper()}] {self.code} @ {locus}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "act": self.act.value,
            "stage": self.stage.value,
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "section_number": self.section_number,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page_no": self.page_no,
        }


@dataclass(slots=True)
class ParseReport:
    """Mutable accumulator for one act's defects, plus the shipping gate."""

    act: Act
    schema_version: str = SCHEMA_VERSION
    defects: list[ParseDefect] = field(default_factory=list)

    def add(
        self,
        stage: PipelineStage,
        severity: DefectSeverity,
        code: str,
        message: str,
        *,
        section_number: str | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        page_no: int | None = None,
    ) -> ParseDefect:
        defect = ParseDefect(
            act=self.act,
            stage=stage,
            severity=severity,
            code=code,
            message=message,
            section_number=section_number,
            char_start=char_start,
            char_end=char_end,
            page_no=page_no,
        )
        self.defects.append(defect)
        return defect

    @property
    def blocking(self) -> list[ParseDefect]:
        return [d for d in self.defects if d.severity is DefectSeverity.BLOCKING]

    @property
    def warnings(self) -> list[ParseDefect]:
        return [d for d in self.defects if d.severity is DefectSeverity.WARNING]

    @property
    def is_shippable(self) -> bool:
        return not self.blocking

    def assert_shippable(self) -> None:
        """Raise unless zero blocking defects. Called before Layer 6."""
        if self.is_shippable:
            return
        preview = "\n  ".join(str(d) for d in self.blocking[:10])
        raise SchemaViolation(
            f"{self.act.value}: {len(self.blocking)} blocking defect(s); "
            f"refusing to embed.\n  {preview}"
        )

    def to_dict(self) -> dict:
        by_code: dict[str, int] = {}
        for defect in self.defects:
            by_code[defect.code] = by_code.get(defect.code, 0) + 1
        return {
            "schema_version": self.schema_version,
            "act": self.act.value,
            "shippable": self.is_shippable,
            "blocking_count": len(self.blocking),
            "warning_count": len(self.warnings),
            "by_code": dict(sorted(by_code.items())),
            "defects": [d.to_dict() for d in self.defects],
        }


# ---------------------------------------------------------------------------
# Canonical output tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chapter:
    """Structural parent, resolved by interval mapping over CHAPTER landmarks."""

    number: str      # roman numeral, e.g. "II"
    title: str

    def __post_init__(self) -> None:
        if not _ROMAN_ONLY_RE.match(self.number):
            raise SchemaViolation(f"chapter number must be roman, got {self.number!r}")
        if not self.title.strip():
            raise SchemaViolation(f"chapter {self.number} has an empty title")

    @property
    def citation(self) -> str:
        return f"Chapter {self.number}"

    def to_dict(self) -> dict:
        return {"number": self.number, "title": self.title}

    @classmethod
    def from_dict(cls, payload: dict) -> "Chapter":
        return cls(number=payload["number"], title=payload["title"])


@dataclass(frozen=True, slots=True)
class TableRow:
    """One row of the BNSS S.359 compounding table.

    Tables are structured data, not prose: flattening them to a text blob
    destroys the offence-to-section mapping that makes the row citable.
    """

    offence_description: str | None = None
    bns_section: str | None = None
    compounded_by_or_details: str | None = None

    @property
    def as_text(self) -> str:
        parts = [
            self.offence_description,
            self.bns_section,
            self.compounded_by_or_details,
        ]
        return " | ".join(p for p in parts if p)

    def to_dict(self) -> dict:
        return {
            "offence_description": self.offence_description,
            "bns_section": self.bns_section,
            "compounded_by_or_details": self.compounded_by_or_details,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TableRow":
        return cls(
            offence_description=payload.get("offence_description"),
            bns_section=payload.get("bns_section"),
            compounded_by_or_details=payload.get("compounded_by_or_details"),
        )


@dataclass(frozen=True, slots=True)
class SubClause:
    """Depth 3: ``(i)``, ``(ii)`` ... beneath a clause."""

    sub_clause_id: str
    content: str

    def __post_init__(self) -> None:
        if not _ALPHA_MARKER_RE.match(self.sub_clause_id):
            raise SchemaViolation(
                f"malformed sub_clause_id {self.sub_clause_id!r}"
            )
        if not self.content.strip():
            raise SchemaViolation(f"sub-clause {self.sub_clause_id} has empty content")

    def leaf_texts(self) -> Iterator[str]:
        yield self.content

    def to_dict(self) -> dict:
        return {"sub_clause_id": self.sub_clause_id, "content": self.content}

    @classmethod
    def from_dict(cls, payload: dict) -> "SubClause":
        return cls(
            sub_clause_id=payload["sub_clause_id"], content=payload["content"]
        )


@dataclass(frozen=True, slots=True)
class Clause:
    """Depth 2: ``(a)``, ``(b)`` ... beneath a sub-section.

    ``intro_text`` is mandatory in the schema even when null. Its absence from
    the legacy BNSS clause shape is precisely how lead-in text ("shall be
    punished with imprisonment which may extend to--") was dropped on the floor
    between a clause marker and its first sub-clause.

    ``residual_text`` captures trailing matter after the last child (provisos,
    Explanations, Illustrations). Without it, coverage can never reach 1.0.
    """

    clause_id: str
    content: str | None = None
    intro_text: str | None = None
    sub_clauses: tuple[SubClause, ...] = ()
    residual_text: str | None = None

    def __post_init__(self) -> None:
        if not _ALPHA_MARKER_RE.match(self.clause_id):
            raise SchemaViolation(f"malformed clause_id {self.clause_id!r}")
        object.__setattr__(self, "sub_clauses", tuple(self.sub_clauses))
        if self.sub_clauses:
            check_marker_sequence(
                [sc.sub_clause_id for sc in self.sub_clauses],
                "sub_clause",
                f"clause {self.clause_id}",
            )
        elif not (self.content or "").strip() and not (self.intro_text or "").strip():
            raise SchemaViolation(
                f"clause {self.clause_id} is empty and has no sub-clauses"
            )

    def leaf_texts(self) -> Iterator[str]:
        for value in (self.intro_text, self.content):
            if value:
                yield value
        for sub_clause in self.sub_clauses:
            yield from sub_clause.leaf_texts()
        if self.residual_text:
            yield self.residual_text

    def to_dict(self) -> dict:
        return {
            "clause_id": self.clause_id,
            "intro_text": self.intro_text,
            "content": self.content,
            "sub_clauses": [sc.to_dict() for sc in self.sub_clauses],
            "residual_text": self.residual_text,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Clause":
        return cls(
            clause_id=payload["clause_id"],
            content=payload.get("content"),
            intro_text=payload.get("intro_text"),
            sub_clauses=tuple(
                SubClause.from_dict(sc) for sc in (payload.get("sub_clauses") or ())
            ),
            residual_text=payload.get("residual_text"),
        )


@dataclass(frozen=True, slots=True)
class SubSection:
    """Depth 1: ``(1)``, ``(2)`` ... beneath a section.

    ``sub_section_id`` is ``None`` for a flat section carrying a single
    unnumbered body. Mixing ``None`` with numbered siblings is rejected: it
    means the boundary detector invented one of them.
    """

    sub_section_id: str | None = None
    content: str | None = None
    intro_text: str | None = None
    clauses: tuple[Clause, ...] = ()
    table_data: tuple[TableRow, ...] = ()
    residual_text: str | None = None

    def __post_init__(self) -> None:
        if self.sub_section_id is not None and not _NUMERIC_MARKER_RE.match(
            self.sub_section_id
        ):
            raise SchemaViolation(
                f"malformed sub_section_id {self.sub_section_id!r}"
            )
        object.__setattr__(self, "clauses", tuple(self.clauses))
        object.__setattr__(self, "table_data", tuple(self.table_data))
        if self.clauses:
            check_marker_sequence(
                [c.clause_id for c in self.clauses],
                "clause",
                f"sub-section {self.sub_section_id}",
            )
        has_prose = bool((self.content or "").strip() or (self.intro_text or "").strip())
        if not has_prose and not self.clauses and not self.table_data:
            raise SchemaViolation(
                f"sub-section {self.sub_section_id} has no content, clauses, or table"
            )

    def leaf_texts(self) -> Iterator[str]:
        for value in (self.intro_text, self.content):
            if value:
                yield value
        for clause in self.clauses:
            yield from clause.leaf_texts()
        for row in self.table_data:
            text = row.as_text
            if text:
                yield text
        if self.residual_text:
            yield self.residual_text

    def to_dict(self) -> dict:
        return {
            "sub_section_id": self.sub_section_id,
            "intro_text": self.intro_text,
            "content": self.content,
            "clauses": [c.to_dict() for c in self.clauses],
            "table_data": [r.to_dict() for r in self.table_data],
            "residual_text": self.residual_text,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "SubSection":
        return cls(
            sub_section_id=payload.get("sub_section_id"),
            content=payload.get("content"),
            intro_text=payload.get("intro_text"),
            clauses=tuple(Clause.from_dict(c) for c in (payload.get("clauses") or ())),
            table_data=tuple(
                TableRow.from_dict(r) for r in (payload.get("table_data") or ())
            ),
            residual_text=payload.get("residual_text"),
        )


#: Any all-caps CHAPTER heading welded into a body. 37 real occurrences in the
#: legacy BNSS output, e.g. "... in force. 18 CHAPTER II CONSTITUTION OF ...".
_CHAPTER_NOISE_RE = re.compile(r"\bCHAPTER\s+[IVXLCDM]{1,8}\b")

#: A section header echoed into the body. 3 real occurrences in legacy BNSS,
#: e.g. "99. 99. Application to High Court ...".
_HEADER_ECHO_RE = re.compile(r"^\s*\d{1,3}[A-Z]{0,3}\s*\.")


@dataclass(frozen=True, slots=True)
class Section:
    """The canonical citation-grade record. One per section, 1,059 total.

    ``text`` is the BODY ONLY -- the "157. Arrest how made.--" header is NOT
    included, because ``section_number`` and ``title`` already carry it. Use
    ``full_text`` to render the conventional inline form. This makes the
    echoed-header defect structurally impossible and stops the title being
    embedded twice.
    """

    act: Act
    section_number: str
    title: str
    text: str
    chapter: Chapter
    punishment_clause: str | None = None
    sub_sections: tuple[SubSection, ...] = ()
    cross_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.act, Act):
            raise SchemaViolation(f"act must be an Act, got {type(self.act)!r}")
        if not _SECTION_NUMBER_RE.match(self.section_number):
            raise SchemaViolation(
                f"{self.act.value}: malformed section_number {self.section_number!r}"
            )
        where = self.citation
        if not self.title or not self.title.strip():
            raise SchemaViolation(f"{where}: title is null or blank")
        if "\n" in self.title:
            raise SchemaViolation(f"{where}: title contains a newline: {self.title!r}")
        if _HEADER_ECHO_RE.match(self.title):
            raise SchemaViolation(
                f"{where}: title starts with an echoed section number: {self.title!r}"
            )
        if not self.text or not self.text.strip():
            raise SchemaViolation(f"{where}: text is empty")
        if _HEADER_ECHO_RE.match(self.text):
            raise SchemaViolation(
                f"{where}: text starts with an echoed section header: "
                f"{self.text[:48]!r}"
            )
        noise = _CHAPTER_NOISE_RE.search(self.text)
        if noise is not None:
            raise SchemaViolation(
                f"{where}: welded chapter heading in text at char "
                f"{noise.start()}: {noise.group(0)!r}"
            )
        if not isinstance(self.chapter, Chapter):
            raise SchemaViolation(f"{where}: chapter must be a Chapter instance")
        object.__setattr__(self, "sub_sections", tuple(self.sub_sections))
        object.__setattr__(self, "cross_refs", tuple(self.cross_refs))
        check_marker_sequence(
            [ss.sub_section_id for ss in self.sub_sections], "sub_section", where
        )

    # --- derived views -----------------------------------------------------

    @property
    def citation(self) -> str:
        return f"{self.act.value} S.{self.section_number}"

    @property
    def full_text(self) -> str:
        """Conventional inline rendering, reconstructed from the parts."""
        return f"{self.section_number}. {self.title}\u2014{self.text}"

    @property
    def has_punishment(self) -> bool:
        return bool(self.punishment_clause and self.punishment_clause.strip())

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.text)

    @property
    def needs_chunking(self) -> bool:
        return self.estimated_tokens > CHUNK_TOKEN_BUDGET

    def leaf_texts(self) -> Iterator[str]:
        """Every terminal string in the hierarchy, in document order."""
        if not self.sub_sections:
            yield self.text
            return
        for sub_section in self.sub_sections:
            yield from sub_section.leaf_texts()

    def coverage_ratio(self) -> float:
        """Fraction of ``text`` accounted for by the hierarchy's leaves.

        The single most important quality signal. It is the ONLY gate that
        catches the BSA S.21 failure mode, where a cross-reference
        ("sub-sections (1) and (2) of section 132") was promoted into two
        phantom sub-sections whose combined content is "and" + "of section
        132." -- a tree that passes every ID check while discarding 96% of the
        operative text.
        """
        baseline = normalize_ws(self.text)
        if not baseline:
            return 0.0
        if not self.sub_sections:
            return 1.0
        harvested = normalize_ws(" ".join(self.leaf_texts()))
        return min(len(harvested) / len(baseline), 1.0)

    def walk_leaves(self) -> Iterator[tuple[str | None, str | None, str | None, str]]:
        """Yield ``(sub_section_id, clause_id, sub_clause_id, text)`` per leaf.

        Feeds the chunker: each tuple is a candidate chunk with a complete
        citation path already attached.
        """
        if not self.sub_sections:
            yield (None, None, None, self.text)
            return
        for sub_section in self.sub_sections:
            ss_id = sub_section.sub_section_id
            for value in (sub_section.intro_text, sub_section.content):
                if value:
                    yield (ss_id, None, None, value)
            for clause in sub_section.clauses:
                for value in (clause.intro_text, clause.content):
                    if value:
                        yield (ss_id, clause.clause_id, None, value)
                for sub_clause in clause.sub_clauses:
                    yield (
                        ss_id,
                        clause.clause_id,
                        sub_clause.sub_clause_id,
                        sub_clause.content,
                    )
                if clause.residual_text:
                    yield (ss_id, clause.clause_id, None, clause.residual_text)
            for index, row in enumerate(sub_section.table_data):
                text = row.as_text
                if text:
                    yield (ss_id, None, None, text)
            if sub_section.residual_text:
                yield (ss_id, None, None, sub_section.residual_text)

    # --- serialisation ----------------------------------------------------

    def to_dict(self) -> dict:
        """Emit the approved canonical schema, key order included."""
        return {
            "act": self.act.value,
            "section_number": self.section_number,
            "title": self.title,
            "text": self.text,
            "chapter": self.chapter.to_dict(),
            "punishment_clause": self.punishment_clause,
            "sub_sections": [ss.to_dict() for ss in self.sub_sections],
            "cross_refs": list(self.cross_refs),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Section":
        return cls(
            act=Act(payload["act"]),
            section_number=payload["section_number"],
            title=payload["title"],
            text=payload["text"],
            chapter=Chapter.from_dict(payload["chapter"]),
            punishment_clause=payload.get("punishment_clause"),
            sub_sections=tuple(
                SubSection.from_dict(ss) for ss in (payload.get("sub_sections") or ())
            ),
            cross_refs=tuple(payload.get("cross_refs") or ()),
        )


# ---------------------------------------------------------------------------
# Retrieval unit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """One embeddable, citable unit.

    Two text fields, deliberately:

    * ``text``      -- verbatim. This is what a user is shown and what a
                       citation points at. Never synthesised.
    * ``embed_text`` -- ``text`` plus a synthetic context header (act, chapter,
                       section title, parent intro). Fed to BGE so an isolated
                       clause like "(c) three years." remains retrievable, and
                       never surfaced to a user.
    """

    act: Act
    section_number: str
    chapter_number: str
    title: str
    text: str
    embed_text: str
    token_count: int
    sub_section_id: str | None = None
    clause_id: str | None = None
    sub_clause_id: str | None = None
    part_index: int = 0
    part_total: int = 1
    has_punishment: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.act, Act):
            raise SchemaViolation(f"act must be an Act, got {type(self.act)!r}")
        if not self.text.strip():
            raise SchemaViolation(f"{self.citation}: chunk text is empty")
        if not self.embed_text.strip():
            raise SchemaViolation(f"{self.citation}: embed_text is empty")
        if self.token_count < 1:
            raise SchemaViolation(
                f"{self.citation}: token_count must be >= 1, got {self.token_count}"
            )
        if self.token_count > BGE_MAX_TOKENS:
            raise SchemaViolation(
                f"{self.citation}: token_count {self.token_count} exceeds the "
                f"BGE window of {BGE_MAX_TOKENS}; the encoder would silently "
                f"truncate and lose text"
            )
        if self.part_total < 1 or not (0 <= self.part_index < self.part_total):
            raise SchemaViolation(
                f"{self.citation}: invalid part {self.part_index}/{self.part_total}"
            )
        if self.sub_clause_id and not self.clause_id:
            raise SchemaViolation(
                f"{self.citation}: sub_clause_id without a parent clause_id"
            )

    @property
    def citation(self) -> str:
        """Pin-cite path, e.g. ``BNS S.2(28)(a)(i)`` or ``BNSS S.4 [2/3]``."""
        parts = f"{self.act.value} S.{self.section_number}"
        for marker in (self.sub_section_id, self.clause_id, self.sub_clause_id):
            if marker:
                parts += marker
        if self.part_total > 1:
            parts += f" [{self.part_index + 1}/{self.part_total}]"
        return parts

    @property
    def structural_path(self) -> str:
        """Stable identity used for the Qdrant point ID."""
        markers = "".join(
            m for m in (self.sub_section_id, self.clause_id, self.sub_clause_id) if m
        )
        return f"{self.act.value}|{self.section_number}|{markers}|{self.part_index}"

    @property
    def point_id(self) -> str:
        """Deterministic UUIDv5 so re-ingestion upserts instead of duplicating.

        Note: if chunk boundaries change, ``part_index`` changes and old points
        become orphans. ``qdrant_loader`` must delete-by-filter on
        ``(act, section_number)`` before upserting that section.
        """
        return str(uuid.uuid5(QDRANT_ID_NAMESPACE, self.structural_path))

    def to_qdrant_payload(self) -> dict:
        """Flat scalars only -- Qdrant indexes payload fields for pre-filtering."""
        payload = {
            "act": self.act.value,
            "section_number": self.section_number,
            "chapter_number": self.chapter_number,
            "title": self.title,
            "citation": self.citation,
            "sub_section_id": self.sub_section_id,
            "clause_id": self.clause_id,
            "sub_clause_id": self.sub_clause_id,
            "part_index": self.part_index,
            "part_total": self.part_total,
            "token_count": self.token_count,
            "has_punishment": self.has_punishment,
            "schema_version": SCHEMA_VERSION,
            "text": self.text,
        }
        return {key: value for key, value in payload.items() if value is not None}


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------


def _expect_violation(label: str, thunk) -> str:
    """Assert ``thunk`` raises SchemaViolation. Returns the message."""
    try:
        thunk()
    except SchemaViolation as exc:
        return str(exc)
    raise AssertionError(f"{label}: expected SchemaViolation, none raised")


def _valid_section(**overrides) -> Section:
    base = dict(
        act=Act.BNSS,
        section_number="35",
        title="When police may arrest without warrant.",
        text="A police officer may without an order from a Magistrate arrest any person.",
        chapter=Chapter(number="V", title="ARREST OF PERSONS"),
    )
    base.update(overrides)
    return Section(**base)


def _self_check() -> int:
    import sys

    checks: list[tuple[str, object]] = []

    # 1 -- act registry
    counts = {act.value: act.expected_sections for act in Act}
    assert counts == {"BNS": 358, "BNSS": 531, "BSA": 170}, counts
    assert TOTAL_EXPECTED_SECTIONS == 1059, TOTAL_EXPECTED_SECTIONS
    assert Act.BNSS.source_pdf == "Bnss_2023.pdf"
    assert Act.BNSS.output_json == "BNSS_2023_parsed.json"
    assert Act.BNSS.qdrant_collection == "nyayaml_bnss"
    checks.append(("Act registry & section counts", "BNS=358 BNSS=531 BSA=170 sum=1059"))

    # 2 -- interval arithmetic
    gaps = subtract_spans(Span(0, 100), [Span(10, 20), Span(18, 30), Span(90, 100)])
    assert [(g.char_start, g.char_end) for g in gaps] == [(0, 10), (30, 90)], gaps
    assert subtract_spans(Span(0, 10), []) == (Span(0, 10),)
    assert subtract_spans(Span(0, 10), [Span(0, 10)]) == ()
    assert Span(0, 5).overlaps(Span(4, 9)) and not Span(0, 5).overlaps(Span(5, 9))
    _expect_violation("inverted span", lambda: Span(10, 4))
    checks.append(("Span arithmetic & interval subtraction", "3 gap cases + 2 guards"))

    # 3 -- page lookup
    doc = RawDocument(
        act=Act.BSA,
        source_path="data/raw/Bsa_2023.pdf",
        source_sha256="0" * 64,
        extractor="pdfplumber==0.11.10",
        extracted_at="2026-09-05T00:00:00Z",
        text="x" * 300,
        pages=(
            PageSpan(page_index=0, char_start=0, char_end=100, page_no=None),
            PageSpan(page_index=1, char_start=100, char_end=200, page_no=17),
            PageSpan(page_index=2, char_start=200, char_end=300, page_no=18),
        ),
    )
    assert doc.page_of(0).page_no is None
    assert doc.page_of(150).page_no == 17
    assert doc.page_of(299).page_no == 18
    assert doc.to_manifest()["page_count"] == 3
    assert len(doc.text_sha256) == 64
    checks.append(("RawDocument page map & manifest", "3 pages, O(log n) lookup"))

    # 4 -- landmark constraints
    chapter_lm = Landmark(
        kind=LandmarkKind.CHAPTER,
        char_start=10,
        char_end=40,
        raw="CHAPTER II CONSTITUTION OF CRIMINAL COURTS",
        roman="II",
        title="CONSTITUTION OF CRIMINAL COURTS",
    )
    assert chapter_lm.span == Span(10, 40)
    _expect_violation(
        "chapter without roman",
        lambda: Landmark(
            kind=LandmarkKind.CHAPTER, char_start=0, char_end=5, raw="CHAPTER"
        ),
    )
    _expect_violation(
        "toc entry without title",
        lambda: Landmark(
            kind=LandmarkKind.TOC_ENTRY,
            char_start=0,
            char_end=5,
            raw="35.",
            number="35",
        ),
    )
    checks.append(("Landmark kind constraints", "2 malformed anchors rejected"))

    # 5 -- section span ordering
    span = SectionSpan(
        act=Act.BNSS,
        section_number="35",
        header=Span(1000, 1060),
        body=Span(1060, 4200),
        title="When police may arrest without warrant.",
        recovery=RecoveryStrategy.TOC_ORACLE,
        title_source="toc",
    )
    assert span.full == Span(1000, 4200) and span.sort_key == 1000
    _expect_violation(
        "body before header",
        lambda: SectionSpan(
            act=Act.BNS,
            section_number="1",
            header=Span(500, 600),
            body=Span(400, 900),
        ),
    )
    checks.append(("SectionSpan offset ordering", "header/body adjacency enforced"))

    # 6 -- happy path
    section = _valid_section(
        sub_sections=(
            SubSection(
                sub_section_id="(1)",
                intro_text="Any police officer may without an order arrest any person--",
                clauses=(
                    Clause(clause_id="(a)", content="who commits a cognizable offence;"),
                    Clause(
                        clause_id="(b)",
                        intro_text="against whom a reasonable complaint exists--",
                        sub_clauses=(
                            SubClause(sub_clause_id="(i)", content="of a cognizable offence;"),
                            SubClause(sub_clause_id="(ii)", content="punishable with death."),
                        ),
                    ),
                ),
            ),
            SubSection(sub_section_id="(2)", content="Subject to section 39."),
        ),
        punishment_clause="imprisonment which may extend to seven years",
    )
    assert section.citation == "BNSS S.35"
    assert section.has_punishment and not section.needs_chunking
    assert section.full_text.startswith("35. When police may arrest")
    leaves = list(section.walk_leaves())
    assert len(leaves) == 6, leaves
    assert leaves[3] == ("(1)", "(b)", "(i)", "of a cognizable offence;")
    checks.append(("Valid Section constructs", f"depth-3 tree, {len(leaves)} leaves"))

    # 7 -- the 8 BNSS null titles
    msg = _expect_violation("null title", lambda: _valid_section(title="   "))
    assert "title is null or blank" in msg
    checks.append(("REJECT blank title", "8 legacy BNSS nulls now impossible"))

    # 8 -- the 37 welded CHAPTER headings
    msg = _expect_violation(
        "welded chapter",
        lambda: _valid_section(
            text="No court shall take cognizance in force. 18 CHAPTER II "
            "CONSTITUTION OF CRIMINAL COURTS AND OFFICES"
        ),
    )
    assert "welded chapter heading" in msg
    checks.append(("REJECT welded CHAPTER heading", "37 legacy BNSS occurrences"))

    # 9 -- the 3 echoed headers
    msg = _expect_violation(
        "echoed header",
        lambda: _valid_section(text="183. Recording of confessions and statements."),
    )
    assert "echoed section header" in msg
    checks.append(("REJECT echoed section header", "BNSS 99 / 183 / 369"))

    # 10 -- the 150 non-monotonic marker runs
    def _bad_run(ids: tuple[str, ...]):
        return lambda: _valid_section(
            sub_sections=tuple(
                SubSection(sub_section_id=i, content="placeholder text.") for i in ids
            )
        )

    for ids in (
        ("(1)", "(3)", "(2)"),                                    # BNSS S.2
        ("(1)", "(2)", "(3)", "(4)", "(5)", "(4)", "(6)"),        # BNSS S.14
        ("(2)", "(3)"),                                            # missing (1)
    ):
        msg = _expect_violation(f"run {ids}", _bad_run(ids))
        assert "dense 1..N sequence" in msg, msg
    # tolerated: amended-act inserts and roman/alpha runs
    _valid_section(
        sub_sections=(
            SubSection(sub_section_id="(1)", content="a."),
            SubSection(sub_section_id="(1A)", content="b."),
            SubSection(sub_section_id="(2)", content="c."),
        )
    )
    Clause(
        clause_id="(a)",
        sub_clauses=(
            SubClause(sub_clause_id="(i)", content="one."),
            SubClause(sub_clause_id="(ii)", content="two."),
            SubClause(sub_clause_id="(iii)", content="three."),
        ),
    )
    checks.append(("REJECT non-monotonic marker run", "3 bad runs, 2 valid runs pass"))

    # 11 -- BSA S.21: passes every ID check, loses 96% of the text
    phantom = _valid_section(
        act=Act.BSA,
        section_number="21",
        title="Admissions in civil cases when relevant.",
        text=(
            "In civil cases no admission is relevant, if it is made either upon "
            "an express condition that evidence of it is not to be given. "
            "Explanation.--Nothing in this section shall be taken to exempt any "
            "advocate from giving evidence of any matter of which he may be "
            "compelled to give evidence under sub-sections (1) and (2) of "
            "section 132."
        ),
        chapter=Chapter(number="II", title="RELEVANCY OF FACTS"),
        sub_sections=(
            SubSection(sub_section_id="(1)", content="and"),
            SubSection(sub_section_id="(2)", content="of section 132."),
        ),
    )
    ratio = phantom.coverage_ratio()
    assert ratio < 0.10, ratio
    assert _valid_section().coverage_ratio() == 1.0
    assert section.coverage_ratio() > 0.0
    checks.append(
        ("DETECT text loss via coverage", f"BSA S.21 phantom tree -> {ratio:.1%}")
    )

    # 12 -- chunk gates
    chunk = Chunk(
        act=Act.BNS,
        section_number="2",
        chapter_number="I",
        title="Definitions.",
        text="of a cognizable offence;",
        embed_text="BNS Chapter I | S.2 Definitions. | (28)(a)(i) of a cognizable offence;",
        token_count=24,
        sub_section_id="(28)",
        clause_id="(a)",
        sub_clause_id="(i)",
        has_punishment=False,
    )
    assert chunk.citation == "BNS S.2(28)(a)(i)", chunk.citation
    assert chunk.point_id == str(uuid.uuid5(QDRANT_ID_NAMESPACE, chunk.structural_path))
    rebuilt = Chunk(
        act=chunk.act,
        section_number=chunk.section_number,
        chapter_number=chunk.chapter_number,
        title=chunk.title,
        text=chunk.text,
        embed_text=chunk.embed_text,
        token_count=chunk.token_count,
        sub_section_id=chunk.sub_section_id,
        clause_id=chunk.clause_id,
        sub_clause_id=chunk.sub_clause_id,
        has_punishment=chunk.has_punishment,
    )
    assert chunk.point_id == rebuilt.point_id
    payload = chunk.to_qdrant_payload()
    assert "has_punishment" in payload and payload["citation"] == "BNS S.2(28)(a)(i)"
    _expect_violation(
        "over-window chunk",
        lambda: Chunk(
            act=Act.BNS,
            section_number="2",
            chapter_number="I",
            title="Definitions.",
            text="x",
            embed_text="x",
            token_count=BGE_MAX_TOKENS + 1,
        ),
    )
    _expect_violation(
        "orphan sub-clause",
        lambda: Chunk(
            act=Act.BNS,
            section_number="2",
            chapter_number="I",
            title="Definitions.",
            text="x",
            embed_text="x",
            token_count=5,
            sub_clause_id="(i)",
        ),
    )
    checks.append(("Chunk citation, 512 gate & point_id", chunk.point_id[:8] + "..."))

    # 13 -- JSON round-trip
    payload = section.to_dict()
    assert list(payload) == [
        "act",
        "section_number",
        "title",
        "text",
        "chapter",
        "punishment_clause",
        "sub_sections",
        "cross_refs",
    ], list(payload)
    assert Section.from_dict(payload) == section
    assert Section.from_dict(payload).to_dict() == payload
    checks.append(("Section JSON round-trip", "key order + equality stable"))

    # 14 -- fail-closed ledger
    report = ParseReport(act=Act.BSA)
    assert report.is_shippable
    report.add(
        PipelineStage.HIERARCHY,
        DefectSeverity.WARNING,
        "coverage_below_target",
        "leaf coverage 0.97",
        section_number="75",
    )
    assert report.is_shippable
    report.assert_shippable()
    report.add(
        PipelineStage.HIERARCHY,
        DefectSeverity.BLOCKING,
        "phantom_subsection",
        "coverage 0.04 -- cross-reference promoted to a node",
        section_number="21",
    )
    assert not report.is_shippable
    msg = _expect_violation("blocking ledger", report.assert_shippable)
    assert "refusing to embed" in msg
    assert report.to_dict()["by_code"] == {
        "coverage_below_target": 1,
        "phantom_subsection": 1,
    }
    checks.append(("ParseReport fail-closed gate", "1 warning ships, 1 blocker halts"))

    width = 42
    print(f"NyayaML ingestion schema self-check  |  schema v{SCHEMA_VERSION}")
    print(
        f"python {sys.version_info.major}.{sys.version_info.minor}"
        f".{sys.version_info.micro}  |  pipeline.models"
    )
    print("-" * 78)
    for index, (label, detail) in enumerate(checks, start=1):
        print(f"[{index:2d}/{len(checks)}] {label.ljust(width, '.')} PASS  {detail}")
    print("-" * 78)
    print(f"ALL {len(checks)} SCHEMA GATES PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())