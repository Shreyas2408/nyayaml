"""Keep citation metadata separate from the source-content JSON."""

from .config import ACTS


def section_id(act, number):
    return f'{act}-2023-{ACTS[act]["sha256"][:12]}-S{number}'


def source_boxes(lines):
    boxes = {}
    for line in lines:
        page = line["p"]
        box = line["bbox"]
        if page not in boxes:
            boxes[page] = list(box)
        else:
            previous = boxes[page]
            boxes[page] = [
                min(previous[0], box[0]),
                min(previous[1], box[1]),
                max(previous[2], box[2]),
                max(previous[3], box[3]),
            ]
    return [{"page_number": page, "bbox": box} for page, box in sorted(boxes.items())]


def citation_entries(
    document, raw_sections, lines, act, document_index, compounding=None
):
    settings = ACTS[act]
    common = {
        "act": act,
        "document_name": document["document_name"],
        "source_sha256": settings["sha256"],
    }
    entries = []
    root = f"/documents/{document_index}"
    for index, (section, raw) in enumerate(zip(document["sections"], raw_sections)):
        entries.append(
            {
                **common,
                "citation_id": section_id(act, section["section_number"]),
                "kind": "section",
                "section_number": section["section_number"],
                "json_pointer": f"{root}/sections/{index}",
                "source_pages": sorted({line["p"] for line in raw["raw_lines"]}),
                "section_regions": source_boxes(raw["raw_lines"]),
            }
        )
    if act == "BNSS":
        for schedule_index, label, page in [
            (0, "SCH1-NOTES", 173),
            (1, "SCH2-INTRO", 220),
        ]:
            schedule = document["schedules"][schedule_index]
            text = schedule["text"]
            if schedule_index == 0:
                text = text.split(schedule["parts"][0]["title"], 1)[0]
            entries.append(
                {
                    **common,
                    "citation_id": f'BNSS-2023-{settings["sha256"][:12]}-{label}',
                    "kind": "schedule_notes",
                    "json_pointer": f"{root}/schedules/{schedule_index}/text",
                    "source_pages": [page],
                    "char_start": 0,
                    "char_end": len(text),
                }
            )
        for subsection_index, source_rows in enumerate(compounding):
            rows = document["sections"][358]["subsections"][subsection_index]["tables"][
                0
            ]["rows"]
            for row_index, (row, source_row) in enumerate(zip(rows, source_rows)):
                entries.append(
                    {
                        **common,
                        "citation_id": row["row_id"],
                        "kind": "table_row",
                        "section_number": "359",
                        "subsection_id": f"({subsection_index + 1})",
                        "json_pointer": f"{root}/sections/358/subsections/{subsection_index}/tables/0/rows/{row_index}",
                        "source_pages": [source_row["page"]],
                        "row_ordinal": row_index + 1,
                        "parent_citation_id": section_id(act, "359"),
                    }
                )
        for part_index, part in enumerate(document["schedules"][0]["parts"]):
            for page_index, fragment in enumerate(part["table"]["pages"]):
                page = int(fragment["page_number"])
                for row_index, row in enumerate(fragment["rows"]):
                    # The existing schedule uses cell arrays. Its IDs live here so
                    # adding IDs does not change that established JSON structure.
                    row_id = f'BNSS-2023-{settings["sha256"][:12]}-SCH1-PART{part_index + 1}-P{page}-R{row_index + 1:03}'
                    entries.append(
                        {
                            **common,
                            "citation_id": row_id,
                            "kind": "schedule_row",
                            "json_pointer": f"{root}/schedules/0/parts/{part_index}/table/pages/{page_index}/rows/{row_index}",
                            "source_pages": [page],
                            "row_ordinal": row_index + 1,
                        }
                    )
        form_starts = [
            line
            for line in lines
            if 220 <= line["p"] <= 279 and line["t"].startswith("FORM No.")
        ]
        for index, form in enumerate(document["schedules"][1]["forms"]):
            start = form_starts[index]["p"]
            end = (
                form_starts[index + 1]["p"] - 1 if index + 1 < len(form_starts) else 279
            )
            entries.append(
                {
                    **common,
                    "citation_id": f'BNSS-2023-{settings["sha256"][:12]}-SCH2-FORM{form["form_number"]}',
                    "kind": "form",
                    "json_pointer": f"{root}/schedules/1/forms/{index}",
                    "source_pages": list(range(start, end + 1)),
                }
            )
    elif act == "BSA":
        entries.append(
            {
                **common,
                "citation_id": f'BSA-2023-{settings["sha256"][:12]}-SCHEDULE',
                "kind": "schedule",
                "json_pointer": f"{root}/schedules/0",
                "source_pages": [52, 53],
            }
        )
    return entries
