"""Create linked, token-bounded chunks without changing the source JSON."""
"""keeping the sections whole up to 480 tokens"""


import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from tokenizers import Tokenizer

from .common import compact, data_hash, file_hash, read_json, require, write_json
from .config import ACTS, OUTPUT_DIR
from .download_tokenizer import REVISION, SHA256, TOKENIZER_PATH

TARGET_TOKENS=480
MAX_TOKENS = 512
CHUNKER_VERSION = 'hierarchy-context-rows-1'
CHILD_LEVELS=(
    ("subsections", "subsection"),
    ("clauses","clauses"),
    ("sub_clauses","sub_clauses"),
)

def token_count(tokenizer,text):
    return len(tokenizer.encode(text,add_special_tokens=True).ids)


def locate(text, fragment, start=0):
    #matching existing text while tolerating PDF whitespace difference.
    positions = [i for i in range(start,len(text)) if not text[i].isspace()]
    searchable = "".join(text[i] for i in positions)
    needle = compact(fragment)
    require(bool(needle), "Cannot locate an empty source fragment.")
    position = searchable.find(needle)
    require(position >= 0, "A chunk source fragment is absent from its parent.")
    return positions[position], positions[position + len(needle) - 1] + 1


def split_text(text, prefix, tokenizer, boundaries=(),limit=TARGET_TOKENS):
    """split a leaf without changing or doroping any source character."""
    if not text:
        return []
    require(0<limit <= MAX_TOKENS, "invalid chunk token limit")
    prefix_cost=token_count(tokenizer,prefix)
    require(prefix_cost < limit - 8,"parent context leaves no room for chunk text")
    paragraphs= sorted(
        set(boundaries) | {match.end() for match in re.finditer(r"\n\s*\n", text)}
    )
    sentences=[match.end() for match in re.finditer(r"[.!?;][\s]+", text)]
    spans=[]
    start =0
    while start < len(text):
        if token_count(tokenizer, prefix+text[start:])<= limit:
            end= len(text)
        else:
            encoded= tokenizer.encode(text[start:], add_special_tokens=False)
            offsets= [(a,b) for a,b in encoded.offsets if b>a]
            require(bool(offsets), "Tokenizer produced no usable offsets")
            budget= limit-prefix_cost-2
            end=start + offsets[min(budget, len(offsets))-1][1]
            #para take precedence, sentence breaks are the next choice
            for candidates in (paragraphs,sentences):
                choices=[
                    pos
                    for pos in candidates
                    if start<pos<=end and token_count(tokenizer, text[start:pos])>= 32
                ]
                if choices:
                    end = choices[-1]
                    break
            while token_count(tokenizer, prefix + text[start:end])> limit:
                end -=1
            require(end > start, "Unable to fit text with its parent context")
        spans.append((start,end))
        start = end
    require("".join(text[a:b] for a,b in spans) == text, "chunking lost text")
    return spans


@dataclass
class Node:
    value: dict
    pointer: str
    start: int
    body_start: int
    end: int
    kind: str
    path: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


def build_tree(section, pointer):
    """Map hierarchy nodes to the original section string, including labels."""
    root = Node(section, pointer, 0, 0, len(section["text"]), "section")

    def add_children(parent):
        source = section["text"][parent.body_start : parent.end]
        populated = [(key, kind) for key, kind in CHILD_LEVELS if parent.value.get(key)]
        for collection, kind in populated:
            cursor = 0
            for index, child in enumerate(parent.value[collection]):
                start, end = locate(source, child["id"] + child["text"], cursor)
                body_start, body_end = locate(
                    source, child["text"], start + len(child["id"])
                )
                require(body_end == end, "Child text does not match its labelled span.")
                node = Node(
                    child,
                    f"{parent.pointer}/{collection}/{index}",
                    parent.body_start + start,
                    parent.body_start + body_start,
                    parent.body_start + end,
                    kind,
                    {**parent.path, kind + "_id": child["id"]},
                )
                parent.children.append(node)
                add_children(node)
                cursor = end
        # Some source sections have direct clauses as well as numbered items.
        # Retain their source order without inventing an intermediate parent.
        parent.children.sort(key=lambda child: child.start)
        require(
            all(
                left.end <= right.start
                for left, right in zip(parent.children, parent.children[1:])
            ),
            "Overlapping child nodes in the canonical JSON.",
        )

    add_children(root)
    return root


def section_heading(act, section, path):
    labels = "".join(path.get(kind + "_id", "") for _, kind in CHILD_LEVELS)
    parts = [f'{act} § {section["section_number"]}{labels}', section["title"]]
    chapter = section.get("chapter", {})
    if chapter.get("number"):
        parts.append(f'Chapter {chapter["number"]}')
    elif chapter.get("title"):
        parts.append(chapter["title"])
    return " | ".join(part for part in parts if part)


def row_display(columns, values):
    require(len(columns) == len(values), "Table column and cell counts differ.")
    # Use the source's column labels, not inferred legal descriptions.
    return "\n".join(
        f'{" ".join(label.split())}: {value}' for label, value in zip(columns, values)
    )


def make_chunks(data, citation_map, tokenizer):
    require(
        citation_map["canonical_data_sha256"] == data_hash(data),
        "Citation map belongs to a different JSON version.",
    )
    entries = citation_map["entries"]
    by_pointer = {entry["json_pointer"]: entry for entry in entries}
    require(len(by_pointer) == len(entries), "Duplicate citation-map pointers.")
    chunks = []
    normal_characters = rows_emitted = tables_replaced = sections_checked = 0

    def emit(
        text,
        heading,
        citation,
        pointer,
        *,
        context=(),
        refs=(),
        offset=0,
        node_pointer=None,
        kind=None,
        path=None,
        row=False,
        atomic=False,
        extra=None,
    ):
        nonlocal normal_characters, rows_emitted
        if not text:
            return
        parent_context = "\n".join(item["text"] for item in context)
        prefix = heading + "\n" + (parent_context + "\n" if parent_context else "")
        if atomic:
            require(
                token_count(tokenizer, prefix + text) <= MAX_TOKENS,
                f'{citation["citation_id"]}: row exceeds 512 tokens with context; '
                "review it rather than truncating or silently splitting the row.",
            )
            spans = [(0, len(text))]
        else:
            spans = split_text(text, prefix, tokenizer)
        for index, (start, end) in enumerate(spans):
            body = text[start:end]
            embedding_text = prefix + body
            identity = {
                "version": CHUNKER_VERSION,
                "tokenizer": REVISION,
                "target": TARGET_TOKENS,
                "maximum": MAX_TOKENS,
                "citation": citation["citation_id"],
                "pointer": pointer,
                "node": node_pointer,
                "start": offset + start,
                "end": offset + end,
                "embedding_text": embedding_text,
                "context_refs": list(refs),
            }
            chunks.append(
                {
                    "chunk_id": "chunk-" + data_hash(identity)[:24],
                    "citation_id": citation["citation_id"],
                    "kind": citation["kind"],
                    "granularity": kind or citation["kind"],
                    "act": citation["act"],
                    "source_sha256": citation["source_sha256"],
                    "source_pages": citation["source_pages"],
                    "json_pointer": pointer,
                    "node_pointer": node_pointer or pointer,
                    "parent_citation_id": citation.get(
                        "parent_citation_id", citation["citation_id"]
                    ),
                    "citation_path": path or {},
                    "context_refs": list(dict.fromkeys(refs)),
                    "context_spans": list(context),
                    "parent_context": parent_context,
                    "header": heading,
                    "text": body,
                    "embedding_text": embedding_text,
                    "token_count": token_count(tokenizer, embedding_text),
                    "char_start": offset + start,
                    "char_end": offset + end,
                    "offset_basis": "row_display_text" if row else "json_string",
                    "fragment_index": index + 1,
                    "fragment_count": len(spans),
                    **(extra or {}),
                }
            )
        if row:
            rows_emitted += 1
        else:
            normal_characters += len(text)

    for document_index, document in enumerate(data["documents"]):
        root_pointer = f"/documents/{document_index}"
        act = next(
            name
            for name, settings in ACTS.items()
            if document["document_name"] == settings["stem"] + ".pdf"
        )
        for section_index, section in enumerate(document["sections"]):
            pointer = f"{root_pointer}/sections/{section_index}"
            text_pointer = pointer + "/text"
            citation = by_pointer[pointer]
            source = section["text"]
            tree = build_tree(section, pointer)
            coverage = []
            common_path = {
                "act": act,
                "section_number": section["section_number"],
                "chapter": section.get("chapter", {}),
            }
            table_context_refs = [
                f"{pointer}/subsections/{i}"
                for i, sub in enumerate(section["subsections"])
                if not sub.get("tables")
            ]

            def intro_span(node):
                end = node.children[0].start if node.children else node.body_start
                if node.value.get("tables"):
                    intro = node.value.get("intro_text", "")
                    require(
                        bool(intro), "A table subsection has no source introduction."
                    )
                    _, end = locate(source, intro, node.body_start)
                start = node.body_start
                while start < end and source[start].isspace():
                    start += 1
                while end > start and source[end - 1].isspace():
                    end -= 1
                if start == end:
                    return []
                return [
                    {
                        "json_pointer": text_pointer,
                        "char_start": start,
                        "char_end": end,
                        "text": source[start:end],
                    }
                ]

            def emit_range(start, end, node, context, ancestors, kind=None):
                if end <= start:
                    return
                emit(
                    source[start:end],
                    section_heading(act, section, node.path),
                    citation,
                    text_pointer,
                    context=context,
                    refs=[pointer, *ancestors, node.pointer],
                    offset=start,
                    node_pointer=node.pointer,
                    kind=kind or node.kind,
                    path={**common_path, **node.path},
                )
                coverage.append((start, end))

            def walk(node, end, context=(), ancestors=()):
                nonlocal tables_replaced
                heading = section_heading(act, section, node.path)
                prefix = (
                    heading + "\n" + "".join(item["text"] + "\n" for item in context)
                )
                # Never let a whole parent swallow a table that needs row chunks.
                contains_table = any(
                    descendant.value.get("tables") for descendant in descendants(node)
                )
                if (
                    not contains_table
                    and token_count(tokenizer, prefix + source[node.start : end])
                    <= TARGET_TOKENS
                ):
                    emit_range(node.start, end, node, context, ancestors)
                    return

                if node.value.get("tables"):
                    tables = node.value["tables"]
                    require(
                        len(tables) == 1,
                        "Review a subsection with multiple table regions.",
                    )
                    table = tables[0]
                    marker = "\n" + table["title"] + "\n"
                    require(
                        marker in node.value["text"],
                        "Missing table title in source text.",
                    )
                    fragment = node.value["text"][
                        node.value["text"].index(marker) + 1 :
                    ]
                    table_start, table_end = locate(source, fragment, node.body_start)
                    require(
                        table_end == node.end, "Unaccounted text after a section table."
                    )
                    emit_range(
                        node.start, table_start, node, context, ancestors, "table_intro"
                    )
                    row_context = [*context, *intro_span(node)]
                    for row_index, row in enumerate(table["rows"]):
                        row_pointer = f"{node.pointer}/tables/0/rows/{row_index}"
                        row_citation = by_pointer[row_pointer]
                        require(
                            row.get("row_id") == row_citation["citation_id"],
                            "Row ID mismatch.",
                        )
                        row_text = row_display(
                            table["columns"],
                            [
                                row[key]
                                for key in ("offence", "bns_section", "compoundable_by")
                            ],
                        )
                        ordinal = row_index + 1
                        emit(
                            row_text,
                            heading + f"\nTable 1 | Row {ordinal}",
                            row_citation,
                            row_pointer,
                            context=row_context,
                            refs=[
                                pointer,
                                *ancestors,
                                node.pointer,
                                *table_context_refs,
                            ],
                            kind="table_row",
                            path={**common_path, **node.path},
                            row=True,
                            atomic=True,
                            extra={
                                "row_id": row["row_id"],
                                "row_ordinal": ordinal,
                                "table_ordinal": 1,
                            },
                        )
                    # A table's trailing separator is layout, not a useful chunk.
                    covered_end = (
                        end if not source[table_end:end].strip() else table_end
                    )
                    coverage.append((table_start, covered_end))
                    tables_replaced += 1
                    emit_range(covered_end, end, node, context, ancestors, "residual")
                    return

                if not node.children:
                    emit_range(node.start, end, node, context, ancestors)
                    return

                child_context = [*context, *intro_span(node)]
                child_ancestors = [*ancestors, node.pointer]
                cursor = node.start
                for index, child in enumerate(node.children):
                    emit_range(
                        cursor, child.start, node, context, ancestors, "residual"
                    )
                    next_start = (
                        node.children[index + 1].start
                        if index + 1 < len(node.children)
                        else end
                    )
                    # Attach separator whitespace to its preceding node.
                    child_end = (
                        next_start
                        if not source[child.end : next_start].strip()
                        else child.end
                    )
                    walk(child, child_end, child_context, child_ancestors)
                    cursor = child_end
                emit_range(cursor, end, node, context, ancestors, "residual")

            walk(tree, len(source))
            cursor = 0
            for start, end in sorted(coverage):
                require(
                    start == cursor and end > start,
                    f"Coverage gap or overlap in {citation['citation_id']}.",
                )
                cursor = end
            require(cursor == len(source), "Section coverage is incomplete.")
            sections_checked += 1
            for index, footnote in enumerate(section.get("footnotes", [])):
                emit(
                    footnote["text"],
                    section_heading(act, section, {}),
                    citation,
                    f"{pointer}/footnotes/{index}/text",
                    refs=[pointer],
                    kind="footnote",
                    path=common_path,
                )

        # Preserve the earlier pipeline's schedule, form and notes coverage.
        for schedule_index, schedule in enumerate(document.get("schedules", [])):
            pointer = f"{root_pointer}/schedules/{schedule_index}"
            heading = f'{act} | {schedule["title"]}'
            if schedule.get("parts"):
                intro = schedule["text"].split(schedule["parts"][0]["title"], 1)[0]
                emit(
                    intro,
                    heading,
                    by_pointer[pointer + "/text"],
                    pointer + "/text",
                    refs=[pointer],
                )
                for part_index, part in enumerate(schedule["parts"]):
                    for page_index, page in enumerate(part["table"]["pages"]):
                        for row_index, row in enumerate(page["rows"]):
                            row_pointer = f"{pointer}/parts/{part_index}/table/pages/{page_index}/rows/{row_index}"
                            row_citation = by_pointer[row_pointer]
                            row_heading = (
                                heading
                                + f'\n{part["title"]} | Page {page["page_number"]} | Row {row_index + 1}'
                            )
                            emit(
                                row_display(part["table"]["columns"], row),
                                row_heading,
                                row_citation,
                                row_pointer,
                                refs=[pointer],
                                row=True,
                                extra={
                                    "row_id": row_citation["citation_id"],
                                    "row_ordinal": row_index + 1,
                                },
                            )
            elif schedule.get("forms"):
                emit(
                    schedule["text"],
                    heading,
                    by_pointer[pointer + "/text"],
                    pointer + "/text",
                    refs=[pointer],
                )
                for index, form in enumerate(schedule["forms"]):
                    form_pointer = f"{pointer}/forms/{index}"
                    emit(
                        form["text"],
                        f'{act} | {form["heading"]} | {form["title"]}',
                        by_pointer[form_pointer],
                        form_pointer + "/text",
                        refs=[form_pointer],
                    )
            else:
                emit(
                    schedule["text"],
                    heading,
                    by_pointer[pointer],
                    pointer + "/text",
                    refs=[pointer],
                )

    ids = [chunk["chunk_id"] for chunk in chunks]
    require(bool(chunks), "No chunks were produced.")
    require(len(ids) == len(set(ids)), "Duplicate chunk IDs.")
    require(
        all(chunk["token_count"] <= MAX_TOKENS for chunk in chunks),
        "A chunk exceeds 512 tokens.",
    )
    require(
        citation_map["canonical_data_sha256"] == data_hash(data),
        "Chunking modified the canonical JSON.",
    )
    return chunks, {
        "chunks": len(chunks),
        "maximum_tokens": max(chunk["token_count"] for chunk in chunks),
        "target_tokens": TARGET_TOKENS,
        "chunks_above_target": sum(
            chunk["token_count"] > TARGET_TOKENS for chunk in chunks
        ),
        "text_characters_partitioned": normal_characters,
        "table_rows_emitted": rows_emitted,
        "section_table_row_chunks": sum(
            chunk["kind"] == "table_row" for chunk in chunks
        ),
        "section_table_regions_replaced_by_rows": tables_replaced,
        "sections_checked": sections_checked,
        "coverage_check": "All section ranges accounted for exactly once; table regions replaced by row chunks. Other emitted strings partitioned without character loss.",
        "requires_context_resolution": True,
        "context_note": "Parent introductions are embedded. Resolve context_refs for full parent provisions, exceptions and table conditions.",
    }


def descendants(node):
    yield node
    for child in node.children:
        yield from descendants(child)

def main():
    parser= argparse.ArgumentParser(description=__doc__)
    
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
        file_hash(args.tokenizer)== SHA256, "."
    )
    tokenizer= Tokenizer.from_file(str(args.tokenizer))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    data= read_json(args.input)
    validation= read_json(args.validation)
    require(
        validation["canonical_data_sha256"] == data_hash(data),
        "Validation report belongs to a different JSON version.",
    )
    require(
        validation["blocking_defects"] == []
        and validation["reference_match_excluding_row_ids"],
        "Resolve the extraction defects before chunking.",
    )
    chunks,report = make_chunks(data,read_json(args.citations), tokenizer)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path= args.output_dir / "chunks.jsonl"
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    report.update(
        chunker_version= CHUNKER_VERSION,
        token_limit= MAX_TOKENS,
        canonical_data_sha256=data_hash(data),
        tokenizer_revision=REVISION,
        tokenizer_sha256=SHA256,
        chunks_sha256=file_hash(output_path),
    )
    write_json(args.output_dir / "chunk_report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()