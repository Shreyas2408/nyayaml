"""Read-only lookup of complete records from the 2023 statutory corpus."""

import json
from collections import Counter
from copy import deepcopy
from threading import Lock

from app.config import settings

_DOCUMENT_ACTS = {
    "Bns_2023.pdf": "BNS",
    "Bnss_2023.pdf": "BNSS",
    "Bsa_2023.pdf": "BSA",
}
_index: dict[tuple[str, str], dict] | None = None
_load_lock = Lock()


class CorpusUnavailable(Exception):
    """The snapshot cannot be read safely; never expose its path to callers."""


def _load() -> dict[tuple[str, str], dict]:
    global _index
    if _index is None:
        with _load_lock:
            if _index is None:
                try:
                    with settings.corpus_path.open(encoding="utf-8") as source:
                        documents = json.load(source)["documents"]
                    index = {}
                    seen = set()
                    for document in documents:
                        name = document["document_name"]
                        act = _DOCUMENT_ACTS[name]
                        if name in seen:
                            raise ValueError("Duplicate document")
                        seen.add(name)
                        for record in document["sections"]:
                            section = record["section_number"]
                            if not isinstance(section, str) or not section:
                                raise ValueError("Invalid section key")
                            key = (act, section)
                            if key in index or {"source_snapshot", "disclaimer"} & record.keys():
                                raise ValueError("Conflicting corpus record")
                            index[key] = record
                    if seen != set(_DOCUMENT_ACTS) or not index:
                        raise ValueError("Incomplete snapshot")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise CorpusUnavailable() from exc
                # Publish only a complete index; the corpus is never modified.
                _index = index
    return _index


def get_section(act: str, section: str) -> dict | None:
    """Return an independent, verbatim corpus record, or None.

    Lookup keys only: trim surrounding whitespace and uppercase the act code.
    Section numbers are corpus strings; no numeric or text normalization occurs.
    """
    record = _load().get((act.strip().upper(), section.strip()))
    return deepcopy(record) if record is not None else None


def list_acts() -> list[dict]:
    """Count the actual indexed records for each act."""
    counts = Counter(act for act, _section in _load())
    return [{"act": act, "section_count": counts[act]} for act in sorted(counts)]
