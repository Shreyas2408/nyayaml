"""Layer 1 -- landmarks. Find and classify the structural anchors in the stream.

Records only. Nothing is deleted, no text is allocated, no boundary is decided;
Layer 2 does that using what we hand it here.

Two regions, split at the enacting formula:

  [0, body_start)   table of contents
  [body_start, end) the act itself

The TOC is not noise -- it is a second, independent statement of the act's
structure, and the legacy parser threw it away. We keep it for two reasons:

  1. Title oracle. Every section number maps to its official title, so Layer 2
     can recover a title it failed to read out of the body (strategy R3).
  2. Chapter authority. BNSS Chapter V ("ARREST OF PERSONS") has NO heading
     anywhere in the body -- section 35 follows 34 with nothing between. A
     body-only chapter map silently files sections 35-45 under Chapter IV. The
     TOC has all 39 chapters, so it wins; body headings only cross-check it.

Section candidates are filtered by the N+1 invariant as they are found. That one
rule doubles as the noise filter: commencement footnotes ("1. 1st day of July,
2024, vide notification...") and BSA's trailing Statement of Objects paragraphs
are dropped because their numbers do not continue the sequence.

Output: data/interim/{ACT}_2023_landmarks.json
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pipeline.models import (
    Act,
    DefectSeverity,
    Landmark,
    LandmarkKind,
    ParseReport,
    PipelineStage,
    SCHEMA_VERSION,
    SchemaViolation,
)
from pipeline.parser import acquire, find_repo_root, interim_dir

#  Terminator glyphs separating a section header from its body. Measured: U+2014
# in 1,053 of 1,059 headers, U+2013 doubled in the remaining few.
DASHES = "\u2014\u2013"

ENACTING_RE = re.compile(r"BE it enacted by Parliament", re.I)

# Line-level patterns, applied to one stripped line at a time.
CHAPTER_LINE = re.compile(r"CHAPTER\s+([IVXLCDM]+)")
PART_LINE = re.compile(r"PART\s+([IVXLCDM]+)")
SCHEDULE_LINE = re.compile(r"THE\s+(?:FIRST|SECOND|THIRD)?\s*SCHEDULE")

# Note the lookahead: BNS 255 reads "255.-Public servant..." with the dash flush
# against the period. Requiring \s here loses it, and with it every section after.
SECTION_LINE = re.compile(r"(\d{1,3})\.(?=[\s%s])\s*(.*)" % DASHES)

# A TOC title stops wrapping when the next line opens a new construct. "THE",
# "ACT NO" and "[" catch the act's title block, which sits at the end of the TOC.
TOC_STOP = re.compile(r"(?:\d{1,3}\.|CHAPTER\b|PART\b|SECTIONS\b|THE\b|ACT NO\b|An Act\b|\[)")

# Measured from the TOC of each act: (chapters, parts, schedules).
EXPECTED = {
    Act.BNS: (20, 0, 0),
    Act.BNSS: (39, 0, 2),
    Act.BSA: (12, 4, 1),
}

@dataclass(frozen=True, slots=True)
class LandmarkSet:
    """Layer 1 output. Consumed by Layer 2 (boundaries) and Layer 4 (metadata)."""

    act: Act
    body_start: int
    titles: dict[str, str]        # section number -> official TOC title
    chapters: dict[str, str]      # chapter roman -> title
    chapter_of: dict[str, str]    # section number -> chapter roman
    sections: tuple[Landmark, ...]
    body_chapters: tuple[Landmark, ...]
    parts: tuple[Landmark, ...]
    schedules: tuple[Landmark, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "act": self.act.value,
            "body_start": self.body_start,
            "counts": {
                "toc_titles": len(self.titles),
                "chapters": len(self.chapters),
                "sections": len(self.sections),
                "body_chapters": len(self.body_chapters),
                "parts": len(self.parts),
                "schedules": len(self.schedules),
            },
            "chapters": self.chapters,
            "chapter_of": self.chapter_of,
            "titles": self.titles,
            "sections": [lm.to_dict() for lm in self.sections],
            "body_chapters": [lm.to_dict() for lm in self.body_chapters],
            "parts": [lm.to_dict() for lm in self.parts],
            "schedules": [lm.to_dict() for lm in self.schedules],
        }


def iter_lines(text: str, start: int = 0, stop: int | None = None):
    """Yield (offset, line) without copying the stream."""
    stop = len(text) if stop is None else stop
    pos = start
    while pos < stop:
        end = text.find("\n", pos, stop)
        if end < 0:
            end = stop
        yield pos, text[pos:end]
        pos = end + 1


def parse_toc(stream: str, body_start: int):
    """Read the TOC into (titles, chapters, chapter_of).

    Titles wrap across lines, so each entry stays open until a line arrives that
    starts a new construct.
    """
    titles: dict[str, str] = {}
    chapters: dict[str, str] = {}
    chapter_of: dict[str, str] = {}
    roman = None
    awaiting_chapter_title = False
    open_number = None
    parts: list[str] = []

    def close():
        if open_number:
            titles[open_number] = " ".join(" ".join(parts).split())

    for _, raw in iter_lines(stream, 0, body_start):
        line = raw.strip()
        if not line:
            continue

        if awaiting_chapter_title:
            chapters[roman] = line
            awaiting_chapter_title = False
            continue

        match = CHAPTER_LINE.fullmatch(line)
        if match:
            close()
            open_number = None
            roman = match.group(1)
            awaiting_chapter_title = True
            continue

        if PART_LINE.fullmatch(line) or SCHEDULE_LINE.fullmatch(line) or line == "SECTIONS":
            close()
            open_number = None
            continue

        match = SECTION_LINE.match(line)
        if match:
            close()
            open_number = match.group(1)
            parts = [match.group(2)]
            if roman:
                chapter_of[open_number] = roman
            continue

        if open_number and not TOC_STOP.match(line):
            parts.append(line)

    close()
    return titles, chapters, chapter_of


def scan_body(stream: str, body_start: int):
    """Collect body landmarks. Sections are gated on the N+1 invariant."""
    sections: list[Landmark] = []
    body_chapters: list[Landmark] = []
    part_marks: list[Landmark] = []
    schedules: list[Landmark] = []
    want = 1
    pending_chapter = None

    for offset, raw in iter_lines(stream, body_start):
        line = raw.strip()
        if not line:
            continue

        if pending_chapter:
            start, roman = pending_chapter
            body_chapters.append(
                Landmark(
                    kind=LandmarkKind.CHAPTER,
                    char_start=start,
                    char_end=offset + len(raw),
                    raw=f"CHAPTER {roman} {line}",
                    roman=roman,
                    title=line,
                )
            )
            pending_chapter = None
            continue

        match = CHAPTER_LINE.fullmatch(line)
        if match:
            pending_chapter = (offset, match.group(1))
            continue

        match = PART_LINE.fullmatch(line)
        if match:
            part_marks.append(
                Landmark(
                    kind=LandmarkKind.PART,
                    char_start=offset,
                    char_end=offset + len(raw),
                    raw=line,
                    roman=match.group(1),
                )
            )
            continue

        if SCHEDULE_LINE.fullmatch(line):
            schedules.append(
                Landmark(
                    kind=LandmarkKind.SCHEDULE,
                    char_start=offset,
                    char_end=offset + len(raw),
                    raw=line,
                )
            )
            continue

        match = SECTION_LINE.match(raw)
        if match and int(match.group(1)) == want:
            sections.append(
                Landmark(
                    kind=LandmarkKind.SECTION_CANDIDATE,
                    char_start=offset,
                    char_end=offset + len(raw),
                    raw=raw,
                    number=match.group(1),
                )
            )
            want += 1

    return sections, body_chapters, part_marks, schedules


def build(act: Act, root: Path, report: ParseReport) -> LandmarkSet:
    doc, _, _ = acquire(act, root=root)
    stream = doc.text

    match = ENACTING_RE.search(stream)
    if not match:
        raise SchemaViolation(f"{act.value}: enacting formula not found; cannot split TOC from body")
    body_start = match.start()

    titles, chapters, chapter_of = parse_toc(stream, body_start)
    sections, body_chapters, parts, schedules = scan_body(stream, body_start)

    exp_chapters, exp_parts, exp_schedules = EXPECTED[act]
    expected_sections = act.expected_sections

    if len(sections) != expected_sections:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.BLOCKING,
            "section_candidate_count",
            f"{act.value}: found {len(sections)} section anchors, expected "
            f"{expected_sections}; sequence stalled at {len(sections) + 1}",
        )

    missing_titles = [str(n) for n in range(1, expected_sections + 1) if str(n) not in titles]
    if missing_titles:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.BLOCKING,
            "toc_oracle_incomplete",
            f"{act.value}: {len(missing_titles)} section(s) absent from the TOC "
            f"oracle, so R3 title recovery cannot cover them: {missing_titles[:12]}",
        )

    if len(chapters) != exp_chapters:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.BLOCKING,
            "toc_chapter_count",
            f"{act.value}: TOC yielded {len(chapters)} chapters, expected {exp_chapters}",
        )

    unassigned = [str(n) for n in range(1, expected_sections + 1) if str(n) not in chapter_of]
    if unassigned:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.BLOCKING,
            "chapter_assignment_gap",
            f"{act.value}: {len(unassigned)} section(s) have no chapter: {unassigned[:12]}",
        )

    # BNSS Chapter V is a known, real gap in the source text layer.
    absent = [r for r in chapters if r not in {lm.roman for lm in body_chapters}]
    if absent:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.WARNING,
            "chapter_heading_absent_from_body",
            f"{act.value}: chapter(s) {absent} present in the TOC but with no body "
            f"heading; chapter assignment falls back to the TOC map",
        )

    if len(parts) != exp_parts:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.WARNING,
            "part_count",
            f"{act.value}: found {len(parts)} PART headings, expected {exp_parts}",
        )
    if len(schedules) != exp_schedules:
        report.add(
            PipelineStage.LANDMARK,
            DefectSeverity.WARNING,
            "schedule_count",
            f"{act.value}: found {len(schedules)} SCHEDULE headings, expected {exp_schedules}",
        )

    return LandmarkSet(
        act=act,
        body_start=body_start,
        titles=titles,
        chapters=chapters,
        chapter_of=chapter_of,
        sections=tuple(sections),
        body_chapters=tuple(body_chapters),
        parts=tuple(parts),
        schedules=tuple(schedules),
    )


def sidecar_path(act: Act, root: Path) -> Path:
    return interim_dir(root) / f"{act.value}_2023_landmarks.json"


def write_sidecar(marks: LandmarkSet, root: Path, report: ParseReport) -> Path:
    path = sidecar_path(marks.act, root)
    payload = marks.to_dict()
    payload["report"] = report.to_dict()
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.landmarks",
        description="Layer 1: classify structural anchors in the extracted streams.",
    )
    parser.add_argument("--act", action="append", choices=[a.value for a in Act])
    # TODO: Point --root at your repo root if you run this from outside ingestion/.
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)

    root = (args.root or find_repo_root()).resolve()
    acts = [Act(v) for v in args.act] if args.act else list(Act)

    print(f"NyayaML Layer 1 -- landmarks  |  schema v{SCHEMA_VERSION}")
    print(f"root {root}")
    print("-" * 92)
    print(f"{'ACT':<5} {'body@':>7} {'toc':>5} {'sects':>6} {'chap':>5} {'body_ch':>8} "
          f"{'part':>5} {'sched':>6}  STATUS")

    failed = False
    total_sections = 0

    for act in acts:
        report = ParseReport(act=act)
        marks = build(act, root, report)
        write_sidecar(marks, root, report)
        total_sections += len(marks.sections)
        status = "OK" if report.is_shippable else "BLOCKED"
        print(
            f"{act.value:<5} {marks.body_start:>7} {len(marks.titles):>5} "
            f"{len(marks.sections):>6} {len(marks.chapters):>5} "
            f"{len(marks.body_chapters):>8} {len(marks.parts):>5} "
            f"{len(marks.schedules):>6}  {status}"
        )
        for defect in report.defects:
            print(f"      {defect}")
        if not report.is_shippable:
            failed = True

    print("-" * 92)
    print(f"{total_sections} section anchors across {len(acts)} act(s)")
    if failed:
        print("LAYER 1 DIRTY -- resolve the defects above before Step 4")
        return 1
    print("LAYER 1 CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())