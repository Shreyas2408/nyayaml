"""Check First Schedule column text and row positions with pdfplumber."""

import bisect
import difflib
import re

import pdfplumber

from .common import compact, require
from .tables import schedule_columns


def audit_schedule(pdf_path, document):
    results = []
    with pdfplumber.open(pdf_path) as pdf:
        for part in document["schedules"][0]["parts"]:
            for fragment in part["table"]["pages"]:
                page_number = int(fragment["page_number"])
                page = pdf.pages[page_number - 1]
                words = page.extract_words(
                    x_tolerance=1, y_tolerance=3, keep_blank_chars=False
                )
                starts = schedule_columns(page_number)
                baselines = {}
                for word in sorted(words, key=lambda glyph: glyph["top"]):
                    nearest = next(
                        (y for y in reversed(baselines) if abs(y - word["top"]) < 2),
                        word["top"],
                    )
                    baselines.setdefault(nearest, []).append(word)
                headers = [
                    glyphs
                    for y, glyphs in sorted(baselines.items())
                    if [
                        glyph["text"]
                        for glyph in sorted(glyphs, key=lambda glyph: glyph["x0"])
                    ]
                    == list(map(str, range(1, len(starts) + 1)))
                ]
                require(
                    len(headers) == 1,
                    f"BNSS page {page_number}: numeric table header is ambiguous.",
                )
                minimum_y = max((glyph["bottom"] for glyph in headers[0]))
                body = [
                    glyph
                    for glyph in page.chars
                    if glyph["top"] >= minimum_y - 0.4 and glyph["top"] < 725
                ]
                columns = [[] for _ in starts]
                for word in body:
                    c = max(
                        0,
                        bisect.bisect_right([x - 1.7 for x in starts], word["x0"]) - 1,
                    )
                    columns[c].append(word)
                failures = []
                bounds = [[] for _ in fragment["rows"]]
                for column_index, source_words in enumerate(columns):
                    grouped = {}
                    for word in sorted(source_words, key=lambda glyph: glyph["top"]):
                        nearest = next(
                            (y for y in reversed(grouped) if abs(y - word["top"]) < 2),
                            word["top"],
                        )
                        grouped.setdefault(nearest, []).append(word)
                    source_words = [
                        glyph
                        for y, glyphs in grouped.items()
                        for glyph in sorted(glyphs, key=lambda glyph: glyph["x0"])
                    ]
                    chars = [
                        (char, glyph["top"], glyph["bottom"])
                        for glyph in source_words
                        for char in compact(glyph["text"])
                    ]
                    source = "".join((c[0] for c in chars))
                    expected = "".join(
                        (compact(row[column_index]) for row in fragment["rows"])
                    )
                    if source != expected:
                        matcher = difflib.SequenceMatcher(
                            None, expected, source, autojunk=False
                        )
                        diffs = [
                            {"expected": expected[a:b], "source": source[c:d]}
                            for tag, a, b, c, d in matcher.get_opcodes()
                            if tag != "equal"
                        ]
                        failures.append(
                            {"column": column_index + 1, "differences": diffs[:5]}
                        )
                        continue
                    cursor = 0
                    for row_index, row in enumerate(fragment["rows"]):
                        length = len(compact(row[column_index]))
                        located = chars[cursor : cursor + length]
                        if located:
                            bounds[row_index].append(
                                (
                                    min((x[1] for x in located)),
                                    max((x[2] for x in located)),
                                )
                            )
                        cursor += length
                overlaps = []
                if not failures:
                    for i in range(1, len(bounds)):
                        if bounds[i - 1] and bounds[i]:
                            prev_end = max((x[1] for x in bounds[i - 1]))
                            current_start = min((x[0] for x in bounds[i]))
                            if current_start < prev_end - 1.5:
                                overlaps.append(
                                    {
                                        "previous_row": i,
                                        "next_row": i + 1,
                                        "overlap": round(prev_end - current_start, 2),
                                    }
                                )
                results.append(
                    {
                        "page": page_number,
                        "rows": len(fragment["rows"]),
                        "column_failures": failures,
                        "row_overlaps": overlaps,
                    }
                )
                page.close()
                if failures or overlaps:
                    print("Review", page_number, failures, overlaps, flush=True)
                elif page_number % 10 == 0:
                    print("Checked through page", page_number, flush=True)
    return results
