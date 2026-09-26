"""Read the two table layouts found in the BNSS source PDF."""

import bisect
import re

from .common import normalize_line, require
from .config import ACTS


def schedule_columns(page_number):
    if page_number in (173, 174):
        return [72, 107.7, 224.9, 294.4, 371.7, 459.3]
    if page_number == 175:
        return [72, 107.7, 215.8, 310.5, 391.6, 459.3]
    if page_number == 176:
        return [72, 107.7, 215.9, 328.6, 391.6, 459.3]
    if page_number in (177, 178):
        return [72, 107.5, 224.8, 333.1, 391.6, 459.3]
    if page_number == 179:
        return [72, 107.7, 224.8, 306, 396.2, 459.3]
    if page_number == 196:
        return [72, 107.5, 224.9, 324, 387.3, 454.1]
    if page_number == 219:
        return [80.7, 251.8, 332.5, 404.8]
    return [72, 107.4, 224.8, 324, 387.2, 459.2]


def schedule_page(page, starts, top, bottom=725):
    segments = []
    for line in page["word_lines"]:
        if not top <= line["y"] < bottom:
            continue
        pieces = []
        for word in line["words"]:
            column = max(
                0, bisect.bisect_right([start - 1.7 for start in starts], word["x"]) - 1
            )
            if pieces and pieces[-1]["column"] == column:
                pieces[-1]["text"] += " " + word["text"]
            else:
                pieces.append({"column": column, "text": word["text"], "y": line["y"]})
        segments.extend(pieces)
    rows = []
    current = None
    last_column = -1
    start_y = 0
    for segment in segments:
        column, y = segment["column"], segment["y"]
        if current is None or (
            column <= 1 and column < last_column and y > start_y + 2
        ):
            current = [""] * len(starts)
            rows.append(current)
            start_y = y
        current[column] += ("\n" if current[column] else "") + segment["text"]
        last_column = column
    numbers = [str(number) for number in range(1, len(starts) + 1)]
    return {
        "page_number": str(page["p"]),
        "rows": [row for row in rows if row != numbers],
    }


def compounding_rows(pages):
    """Return the 37 and 13 source rows, plus the page of each row."""
    tables = [[], []]
    regions = [
        (123, 175, 720, 260, 380, 0),
        (124, 110, 720, 306, 370, 0),
        (125, 90, 375, 306, 370, 0),
        (125, 545, 710, 282, 380, 1),
        (126, 105, 555, 285, 380, 1),
    ]
    reference = re.compile(r"^(\d+(?:\([^()]+\))?(?:,\s*\d+(?:\([^()]+\))?)*)\s*(.*)$")
    for page_number, top, bottom, second, third, table_index in regions:
        lines = [
            line
            for line in pages[page_number - 1]["lines"]
            if top <= line["bbox"][1] < bottom
        ]
        starts = []
        for line in lines:
            if second <= line["bbox"][0] < third:
                match = reference.fullmatch(line["t"])
                require(
                    match is not None,
                    f"BNSS page {page_number}: unreadable table reference.",
                )
                starts.append(line["bbox"][1])
        starts.sort()
        rows = [{"page": page_number, "cells": ["", "", ""]} for _ in starts]
        for line in lines:
            x, y = line["bbox"][:2]
            candidates = [index for index, start in enumerate(starts) if start <= y + 1]
            require(
                bool(candidates),
                f"BNSS page {page_number}: table row boundary needs review.",
            )
            row_index = max(candidates)
            column = 0 if x < second else (1 if x < third else 2)
            if column == 1:
                # Two source lines contain the reference and third-column text together.
                match = reference.fullmatch(line["t"])
                pieces = [(1, match[1])]
                if match[2]:
                    pieces.append((2, match[2]))
            else:
                pieces = [(column, line["t"])]
            for cell, text in pieces:
                rows[row_index]["cells"][cell] += (
                    " " if rows[row_index]["cells"][cell] else ""
                ) + normalize_line(text)
        tables[table_index].extend(rows)
    require(
        [len(rows) for rows in tables] == [37, 13], "BNSS 359: table row count changed."
    )
    return tables


def add_compounding_tables(document, pages):
    section = next(
        section
        for section in document["sections"]
        if section["section_number"] == "359"
    )
    tables = compounding_rows(pages)
    regions = [(123, 129, 155, 230, 390), (125, 487, 526, 282, 380)]
    source_version = ACTS["BNSS"]["sha256"][:12]
    for index, (page_number, top, bottom, second, third) in enumerate(regions):
        subsection = section["subsections"][index]
        title = ["TABLE", "Table"][index]
        require(
            any(line["t"] == title for line in pages[page_number - 1]["lines"]),
            "Source table heading missing.",
        )
        intro, _ = subsection["text"].split("\n" + title + "\n", 1)
        columns = [[], [], []]
        for line in pages[page_number - 1]["lines"]:
            x, y = line["bbox"][:2]
            if top <= y < bottom:
                column = 0 if x < second else (1 if x < third else 2)
                columns[column].append(line["t"])
        rows = []
        for row_number, source_row in enumerate(tables[index], 1):
            row = dict(
                zip(["offence", "bns_section", "compoundable_by"], source_row["cells"])
            )
            # This ID is generated metadata. The Gazette does not print row numbers.
            row["row_id"] = (
                f"BNSS-2023-{source_version}-S359-SS{index + 1}-T1-R{row_number:03}"
            )
            rows.append(row)
        subsection["intro_text"] = intro
        subsection["tables"] = [
            {
                "title": title,
                "columns": ["\n".join(column) for column in columns],
                "rows": rows,
            }
        ]
    return tables
