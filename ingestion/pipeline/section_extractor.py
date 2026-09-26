"""Find sections and preserve the chapter and heading sequence."""

import copy
import re

from .common import join_lines, require
from .config import ACTS

BSA_GROUPS = {
    "Closely connected facts",
    "Admissions",
    "Statements by persons who cannot be called as witnesses",
    "Statements made under special circumstances",
    "How much of a statement is to be proved",
    "Judgments of Courts when relevant",
    "Opinions of third persons when relevant",
    "Character when relevant",
    "Public documents",
    "Presumptions as to documents",
}


def section_start(line):
    span = next((span for span in line["spans"] if span["text"].strip()), {})
    if "Bold" in span.get("font", ""):
        return re.match(r"^(\d+)\.", line["t"])
    return None


def chapter_map(lines, first_page):
    chapter = {"number": "", "title": ""}
    result = {}
    index = 0
    while index < len(lines) and lines[index]["p"] < first_page:
        text = lines[index]["t"]
        match = re.fullmatch(r"CHAPTER\s*([IVXLCDM]+)", text)
        if match:
            index += 1
            title = []
            while (
                index < len(lines)
                and lines[index]["t"].isupper()
                and lines[index]["t"] not in ("SECTIONS", "SECTIONS.")
                and not re.match(r"^(CHAPTER|PART)", lines[index]["t"])
            ):
                title.append(lines[index]["t"])
                index += 1
            chapter = {"number": match[1], "title": " ".join(title)}
            continue
        match = re.match(r"^(\d+)\.\s*", text)
        if match:
            result[match[1]] = copy.deepcopy(chapter)
        index += 1
    return result


def split_header(lines):
    first = lines[0]
    number = re.match(r"^(\d+)\.\s*[—–]*\s*", first["t"])
    require(
        number is not None, f'Cannot identify a section number on page {first["p"]}.'
    )
    remaining = copy.deepcopy(lines)
    remaining[0]["t"] = remaining[0]["t"][number.end() :]
    title = []
    for index, line in enumerate(remaining):
        delimiter = re.search(r"\s*(?:—|––|–|--)+\s*", line["t"])
        if delimiter:
            title.append(line["t"][: delimiter.start()])
            body = remaining[index + 1 :]
            inline_text = line["t"][delimiter.end() :]
            if inline_text:
                inline = copy.deepcopy(line)
                inline.update(t=inline_text, x=first["x"], header_inline=True)
                body.insert(0, inline)
            return number[1], " ".join(title), body
        title.append(line["t"])
        if index > 5:
            break
    raise ValueError(f"Section {number[1]}: heading delimiter needs source review.")


def is_group(line, act):
    if line["x"] <= 120:
        return False
    text = line["t"]
    if act == "BNS":
        return text.startswith("Of ") or text == "of abetment"
    if act == "BNSS":
        return re.match(r"^[A-E]\.[—–]", text) is not None
    return text in BSA_GROUPS


def extract_sections(lines, act):
    settings = ACTS[act]
    first, last = settings["first"], settings["last"]
    contents = chapter_map(lines, first)
    main = [line for line in lines if first <= line["p"] <= last]
    footnote_top = 690 if act == "BNSS" else 730
    footnotes = [
        line
        for line in main
        if line["p"] == first
        and line["y"] > footnote_top
        and sum(len(span["text"]) for span in line["spans"] if span["size"] < 10.1)
        > len(line["t"]) * 0.8
    ]
    footnote_ids = {line["uid"] for line in footnotes}
    main = [line for line in main if line["uid"] not in footnote_ids]
    sections, raw_sections, events, opening = [], [], [], []
    pending = []
    chapter = {"number": "", "title": ""}
    part = None
    group = ""

    def finish_section():
        if not pending:
            return
        number, title, body = split_header(pending)
        section = {
            "section_number": number,
            "title": title,
            "chapter": copy.deepcopy(contents.get(number, chapter)),
            "text": join_lines(body),
            "subsections": [],
            "residual_text": "",
        }
        if part:
            section["part"] = copy.deepcopy(part)
        if group:
            section["group_heading"] = group
        if number == "1" and footnotes:
            text = join_lines(footnotes)
            label = re.match(r"^(\d+)\.\s*", text)
            section["footnotes"] = [
                {
                    "id": label[1] if label else "",
                    "text": text[label.end() :] if label else text,
                }
            ]
        sections.append(section)
        raw_sections.append({"lines": body, "raw_lines": copy.deepcopy(pending)})
        pending.clear()

    index = 0
    while index < len(main):
        line = main[index]
        text = line["t"]
        heading = re.fullmatch(r"(CHAPTER|PART)\s*([IVXLCDM]+)", text)
        if heading:
            finish_section()
            heading_page = line["p"]
            index += 1
            title = []
            while (
                index < len(main)
                and main[index]["t"].isupper()
                and not re.match(r"^(CHAPTER|PART)", main[index]["t"])
                and not section_start(main[index])
            ):
                title.append(main[index]["t"])
                index += 1
            value = {"number": heading[2], "title": " ".join(title)}
            if heading[1] == "CHAPTER":
                chapter = value
                group = ""
            else:
                part = value
            events.append(
                {
                    "type": (
                        "chapter_heading" if heading[1] == "CHAPTER" else "part_heading"
                    ),
                    **value,
                    "page_number": str(heading_page),
                }
            )
            continue
        if is_group(line, act):
            finish_section()
            group = text
            events.append(
                {"type": "subheading", "text": text, "page_number": str(line["p"])}
            )
        elif section_start(line):
            finish_section()
            pending.append(line)
            events.append({"type": "section", "section_number": section_start(line)[1]})
        elif pending:
            pending.append(line)
        else:
            opening.append(line)
        index += 1
    finish_section()
    require(
        [section["section_number"] for section in sections]
        == [str(number) for number in range(1, settings["sections"] + 1)],
        f"{act}: section numbers are missing, duplicated or out of order.",
    )
    front = [line for line in lines if line["p"] < first] + opening
    document = {
        "document_name": settings["stem"] + ".pdf",
        "front_matter": {"text": join_lines(front)},
        "body_structure": events,
        "sections": sections,
    }
    return document, raw_sections
