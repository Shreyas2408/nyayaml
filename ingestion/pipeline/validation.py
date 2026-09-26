"""Check content conservation and compare the rebuild with the delivered JSON."""

import copy
import re
from collections import Counter

from .common import compact, require
from .config import ACTS


def without_row_ids(value):
    if isinstance(value, dict):
        return {
            key: without_row_ids(item) for key, item in value.items() if key != "row_id"
        }
    if isinstance(value, list):
        return [without_row_ids(item) for item in value]
    return value


def first_difference(left, right, path="$"):
    if type(left) != type(right):
        return path + ": different types"
    if isinstance(left, dict):
        if set(left) != set(right):
            return path + ": different keys"
        for key in left:
            difference = first_difference(left[key], right[key], path + "." + key)
            if difference:
                return difference
    elif isinstance(left, list):
        if len(left) != len(right):
            return path + ": different list lengths"
        for index, (item, expected) in enumerate(zip(left, right)):
            difference = first_difference(item, expected, f"{path}[{index}]")
            if difference:
                return difference
    elif left != right:
        return path + ": different value"
    return ""


def compare_reference(data, reference):
    names = {document["document_name"] for document in data["documents"]}
    expected = {
        "documents": [
            document
            for document in reference["documents"]
            if document["document_name"] in names
        ]
    }
    difference = first_difference(without_row_ids(data), expected)
    require(not difference, f"Output differs from the delivered JSON: {difference}")


def validate_children(parent):
    for collection in ["subsections", "clauses", "sub_clauses"]:
        children = parent.get(collection, [])
        ids = [child["id"] for child in children]
        require(len(ids) == len(set(ids)), "Duplicate sibling provision IDs.")
        for child in children:
            require(
                re.fullmatch(r"\((?:\d+|[A-Za-z]+)\)", child["id"]) is not None,
                "Invalid provision label.",
            )
            require(
                isinstance(child["text"], str)
                and isinstance(child["residual_text"], str),
                "Missing text field.",
            )
            require(
                compact(child["text"]) in compact(parent["text"]),
                "A child text is not contained in its parent.",
            )
            validate_children(child)


def validate_document(document, raw_sections, pages, lines, act, diagnostics):
    settings = ACTS[act]
    require(len(pages) == settings["pages"], f"{act}: page count mismatch.")
    numbers = [section["section_number"] for section in document["sections"]]
    require(
        numbers == [str(number) for number in range(1, settings["sections"] + 1)],
        f"{act}: section sequence mismatch.",
    )
    require(
        not diagnostics["duplicate_ids"] and not diagnostics["sequence_gaps"],
        f"{act}: hierarchy sequence needs review.",
    )
    require(
        len({section["chapter"]["number"] for section in document["sections"]})
        == settings["chapters"],
        f"{act}: chapter count mismatch.",
    )
    require(
        sum(event["type"] == "subheading" for event in document["body_structure"])
        == settings["groups"],
        f"{act}: subheading count mismatch.",
    )
    for section, raw in zip(document["sections"], raw_sections):
        require(
            all(
                key in section
                for key in [
                    "section_number",
                    "title",
                    "chapter",
                    "text",
                    "subsections",
                    "residual_text",
                ]
            ),
            "Missing section field.",
        )
        source = compact("\n".join(line["t"] for line in raw["raw_lines"]))
        source = re.sub(
            r"^" + re.escape(section["section_number"]) + r"\.[—–]*", "", source
        )
        title = compact(section["title"])
        require(
            source.startswith(title),
            f'{act} {section["section_number"]}: title mismatch.',
        )
        body = re.sub(r"^[—–-]+", "", source[len(title) :])
        require(
            body == compact(section["text"]),
            f'{act} {section["section_number"]}: body text changed.',
        )
        validate_children(section)
    if act == "BNSS":
        require(
            len(document["schedules"][1]["forms"]) == 58,
            "BNSS: missing statutory form.",
        )
        table_pages = 0
        for part in document["schedules"][0]["parts"]:
            for fragment in part["table"]["pages"]:
                number = int(fragment["page_number"])
                source = [line for line in lines if line["p"] == number]
                if number == 173:
                    source = [line for line in source if 310 < line["y"] < 725]
                elif number == 219:
                    source = [line for line in source if 154 < line["y"] < 725]
                else:
                    source = [
                        line
                        for line in source
                        if line["y"] < 725
                        and not (
                            line["y"] < 110
                            and re.fullmatch(r"[1-6](?: [1-6])*", line["t"])
                        )
                    ]
                source_chars = Counter(compact("\n".join(line["t"] for line in source)))
                table_chars = Counter(
                    compact("".join(cell for row in fragment["rows"] for cell in row))
                )
                require(
                    source_chars == table_chars,
                    f"BNSS schedule page {number}: table content changed.",
                )
                require(
                    all(
                        len(row) == len(part["table"]["columns"])
                        for row in fragment["rows"]
                    ),
                    "Table width mismatch.",
                )
                table_pages += 1
        require(table_pages == 47, "BNSS: expected all 47 First Schedule pages.")
        tables = [
            subsection["tables"][0]
            for subsection in document["sections"][358]["subsections"][:2]
        ]
        require(
            [len(table["rows"]) for table in tables] == [37, 13],
            "BNSS 359: table count mismatch.",
        )
        row_ids = [row["row_id"] for table in tables for row in table["rows"]]
        require(
            len(row_ids) == len(set(row_ids)) == 50,
            "BNSS 359: missing or duplicate row IDs.",
        )
    return {
        "act": act,
        "pages": len(pages),
        "sections": len(numbers),
        "section_text_characters": sum(
            len(section["text"]) for section in document["sections"]
        ),
        "content_checks_passed": True,
        "hierarchy_sequence_checks_passed": True,
    }
