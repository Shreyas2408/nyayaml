"""Create linked, token-bounded chunks without changing the source JSON."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from tokenizers import Tokenizer

from .common import compact, data_hash, file_hash, read_json, require, write_json
from .config import ACTS, OUTPUT_DIR
from .download_tokenizer import REVISION, SHA256, TOKENIZER_PATH
from .metadata import section_id

MAX_TOKENS = 512
CHUNKER_VERSION = 'hierarchy-rows-1'


def row_display(columns, values):
    # Labels come from the printed headers; values stay unchanged.
    return "\n".join(
        f'{" ".join(label.split())}: {value}' for label, value in zip(columns, values)
    )


def locate(text, fragment, start=0):
    positions = [
        index
        for index, char in enumerate(text)
        if not char.isspace() and index >= start
    ]
    searchable = "".join(text[index] for index in positions)
    needle = compact(fragment)
    require(bool(needle), "Cannot locate an empty source fragment.")
    position = searchable.find(needle)
    require(position >= 0, "A chunk source fragment is absent from its parent.")
    return positions[position], positions[position + len(needle) - 1] + 1


def hierarchy_boundaries(section):
    boundaries = {0, len(section["text"])}

    def visit(node, offset):
        for collection in ["subsections", "clauses", "sub_clauses"]:
            cursor = 0
            for child in node.get(collection, []):
                start, end = locate(node["text"], child["id"] + child["text"], cursor)
                boundaries.update([offset + start, offset + end])
                # Child text excludes its own label; locate its actual body separately.
                body_start, _ = locate(
                    node["text"], child["text"], start + len(child["id"])
                )
                visit(child, offset + body_start)
                cursor = end

    visit(section, 0)
    return boundaries


def token_count(tokenizer, text):
    return len(tokenizer.encode(text, add_special_tokens=True).ids)


def split_text(text, prefix, tokenizer, boundaries=()):
    """Return disjoint source slices. Tokenizer offsets never rewrite the text."""
    if not text:
        return []
    prefix_cost = token_count(tokenizer, prefix)
    require(
        prefix_cost < MAX_TOKENS - 8, "Chunk heading leaves no room for source text."
    )
    preferred = sorted(
        set(boundaries)
        | {match.end() for match in re.finditer(r"\n\s*\n|(?<=[.;])\n", text)}
    )
    spans = []
    start = 0
    while start < len(text):
        remaining = text[start:]
        if token_count(tokenizer, prefix + remaining) <= MAX_TOKENS:
            end = len(text)
        else:
            encoded = tokenizer.encode(remaining, add_special_tokens=False)
            budget = MAX_TOKENS - prefix_cost - 2
            offsets = [pair for pair in encoded.offsets if pair[1] > pair[0]]
            require(bool(offsets), "Tokenizer produced no usable source offsets.")
            end = start + offsets[min(budget, len(offsets)) - 1][1]
            choices = [position for position in preferred if start < position <= end]
            if choices:
                # Avoid tiny chunks if a paragraph break is right next to the start.
                useful = [
                    position
                    for position in choices
                    if token_count(tokenizer, text[start:position]) >= 32
                ]
                if useful:
                    end = useful[-1]
            while token_count(tokenizer, prefix + text[start:end]) > MAX_TOKENS:
                end -= 1
            require(
                end > start, "Unable to split a source passage within the token limit."
            )
        spans.append((start, end))
        start = end
    require(
        "".join(text[start:end] for start, end in spans) == text,
        "Chunking lost source characters.",
    )
    return spans


def make_chunks(data, citation_map, tokenizer):
    require(
        citation_map["canonical_data_sha256"] == data_hash(data),
        "Citation map belongs to a different JSON version.",
    )
    by_pointer = {entry["json_pointer"]: entry for entry in citation_map["entries"]}
    chunks = []
    normal_characters = 0
    rows_emitted = 0
    section_tables_replaced = 0

    def emit(
        text,
        heading,
        citation,
        pointer,
        context_refs=(),
        boundaries=(),
        offset=0,
        row=False,
    ):
        nonlocal normal_characters, rows_emitted
        prefix = heading + "\n"
        pieces = split_text(text, prefix, tokenizer, boundaries)
        for start, end in pieces:
            body = text[start:end]
            signature = f'{CHUNKER_VERSION}|{REVISION}|{MAX_TOKENS}|{citation["citation_id"]}|{pointer}|{offset + start}|{offset + end}|{prefix}{body}'
            chunk_id = (
                "chunk-" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
            )
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "citation_id": citation["citation_id"],
                    "kind": citation["kind"],
                    "act": citation["act"],
                    "source_sha256": citation["source_sha256"],
                    "source_pages": citation["source_pages"],
                    "json_pointer": pointer,
                    "parent_citation_id": citation.get(
                        "parent_citation_id", citation["citation_id"]
                    ),
                    "context_refs": list(dict.fromkeys(context_refs)),
                    "text": body,
                    "embedding_text": prefix + body,
                    "token_count": token_count(tokenizer, prefix + body),
                    "char_start": offset + start,
                    "char_end": offset + end,
                    "offset_basis": "row_display_text" if row else "json_string",
                }
            )
        if row:
            rows_emitted += 1
        else:
            normal_characters += len(text)

    for document_index, document in enumerate(data["documents"]):
        root = f"/documents/{document_index}"
        act = next(
            name
            for name, settings in ACTS.items()
            if document["document_name"] == settings["stem"] + ".pdf"
        )
        for section_index, section in enumerate(document["sections"]):
            pointer = f"{root}/sections/{section_index}"
            citation = by_pointer[pointer]
            heading = f'{act} {section["section_number"]}. {section["title"]}'
            boundaries = hierarchy_boundaries(section)
            table_intervals = []
            # Tables with row objects are emitted as rows. Their linear text is
            # excluded from ordinary chunks to avoid indexing the same table twice.
            for subsection_index, subsection in enumerate(section["subsections"]):
                if not subsection.get("tables"):
                    continue
                sub_pointer = f"{pointer}/subsections/{subsection_index}"
                body_start, body_end = locate(section["text"], subsection["text"])
                table = subsection["tables"][0]
                marker = "\n" + table["title"] + "\n"
                table_fragment = subsection["text"][
                    subsection["text"].index(marker) + 1 :
                ]
                table_start, table_end = locate(
                    section["text"], table_fragment, body_start
                )
                require(
                    table_end == body_end,
                    "Table region does not end with its subsection.",
                )
                table_intervals.append((table_start, body_end))
                section_tables_replaced += 1
                for table_index, table in enumerate(subsection["tables"]):
                    for row_index, row in enumerate(table["rows"]):
                        row_pointer = (
                            f"{sub_pointer}/tables/{table_index}/rows/{row_index}"
                        )
                        row_citation = by_pointer[row_pointer]
                        row_text = row_display(
                            table["columns"],
                            [
                                row[key]
                                for key in ["offence", "bns_section", "compoundable_by"]
                            ],
                        )
                        table_heading = heading + "\n" + subsection["intro_text"]
                        # Later retrieval must resolve these conditions, not treat
                        # a matching table row as a complete statement of the law.
                        conditions = [
                            f"{pointer}/subsections/{index}"
                            for index in range(2, len(section["subsections"]))
                        ]
                        emit(
                            row_text,
                            table_heading,
                            row_citation,
                            row_pointer,
                            [pointer, sub_pointer] + conditions,
                            row=True,
                        )
            start = 0
            for table_start, table_end in sorted(table_intervals) + [
                (len(section["text"]), len(section["text"]))
            ]:
                part = section["text"][start:table_start]
                local_boundaries = [
                    position - start
                    for position in boundaries
                    if start < position < table_start
                ]
                emit(
                    part,
                    heading,
                    citation,
                    pointer + "/text",
                    [pointer],
                    local_boundaries,
                    start,
                )
                start = table_end
            for footnote_index, footnote in enumerate(section.get("footnotes", [])):
                emit(
                    footnote["text"],
                    heading,
                    citation,
                    f"{pointer}/footnotes/{footnote_index}/text",
                    [pointer],
                )

        for schedule_index, schedule in enumerate(document.get("schedules", [])):
            pointer = f"{root}/schedules/{schedule_index}"
            if schedule.get("parts"):
                first_title = schedule["parts"][0]["title"]
                intro = schedule["text"].split(first_title, 1)[0]
                schedule_citation = by_pointer[pointer + "/text"]
                emit(
                    intro,
                    schedule["title"],
                    schedule_citation,
                    pointer + "/text",
                    [pointer],
                )
                for part_index, part in enumerate(schedule["parts"]):
                    for page_index, page in enumerate(part["table"]["pages"]):
                        for row_index, row in enumerate(page["rows"]):
                            row_pointer = f"{pointer}/parts/{part_index}/table/pages/{page_index}/rows/{row_index}"
                            citation = by_pointer[row_pointer]
                            heading = f'{act} {schedule["title"]}\n{part["title"]}'
                            emit(
                                row_display(part["table"]["columns"], row),
                                heading,
                                citation,
                                row_pointer,
                                [pointer],
                                row=True,
                            )
            elif schedule.get("forms"):
                emit(
                    schedule["text"],
                    f'{act} {schedule["title"]}',
                    by_pointer[pointer + "/text"],
                    pointer + "/text",
                    [pointer],
                )
                for form_index, form in enumerate(schedule["forms"]):
                    form_pointer = f"{pointer}/forms/{form_index}"
                    citation = by_pointer[form_pointer]
                    emit(
                        form["text"],
                        f'{act} {form["heading"]}\n{form["title"]}',
                        citation,
                        form_pointer + "/text",
                        [form_pointer],
                    )
            else:
                citation = by_pointer[pointer]
                emit(
                    schedule["text"],
                    f'{act} {schedule["title"]}',
                    citation,
                    pointer + "/text",
                    [pointer],
                )

    # Front matter and statements remain in the source JSON and are not indexed
    # as operative provisions by this default chunking policy.
    ids = [chunk["chunk_id"] for chunk in chunks]
    require(len(ids) == len(set(ids)), "Duplicate chunk IDs.")
    require(
        all(chunk["token_count"] <= MAX_TOKENS for chunk in chunks),
        "A chunk exceeds the model limit.",
    )
    return chunks, {
        "chunks": len(chunks),
        "maximum_tokens": max(chunk["token_count"] for chunk in chunks),
        "text_characters_partitioned": normal_characters,
        "table_rows_emitted": rows_emitted,
        "section_table_regions_replaced_by_rows": section_tables_replaced,
        "coverage_check": "Every emitted string was partitioned without character loss; table rows replace their linearized table regions.",
        "requires_context_resolution": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=OUTPUT_DIR / "Bns_Bnss_Bsa_2023_final.json"
    )
    parser.add_argument("--citations", type=Path, default=OUTPUT_DIR / "citations.json")
    parser.add_argument(
        "--validation", type=Path, default=OUTPUT_DIR / "validation_report.json"
    )
    parser.add_argument("--tokenizer", type=Path, default=TOKENIZER_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    require(
        file_hash(args.tokenizer) == SHA256,
        "Use the pinned BGE tokenizer from pipeline.download_tokenizer.",
    )
    tokenizer = Tokenizer.from_file(str(args.tokenizer))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    data = read_json(args.input)
    validation = read_json(args.validation)
    require(
        validation["canonical_data_sha256"] == data_hash(data),
        "Validation report belongs to a different JSON version.",
    )
    require(
        validation["blocking_defects"] == []
        and validation["reference_match_excluding_row_ids"],
        "Resolve the extraction defects before chunking.",
    )
    chunks, report = make_chunks(data, read_json(args.citations), tokenizer)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "chunks.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    report.update(
        chunker_version=CHUNKER_VERSION,
        token_limit=MAX_TOKENS,
        canonical_data_sha256=data_hash(data),
        tokenizer_revision=REVISION,
        tokenizer_sha256=SHA256,
    )
    write_json(args.output_dir / "chunk_report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
