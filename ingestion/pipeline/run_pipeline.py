"""Build the same source JSON, with row IDs added to Section 359."""

import argparse
from pathlib import Path

from .audit_tables import audit_schedule
from .common import data_hash, file_hash, read_json, require, write_json
from .config import (
    ACTS,
    INTERIM_DIR,
    OUTPUT_DIR,
    PDF_DIR,
    PYMUPDF_VERSION,
    REFERENCE,
    REFERENCE_SHA256,
)
from .hierarchy import structure
from .metadata import citation_entries
from .parser import acquire, prepare_lines
from .section_extractor import extract_sections
from .supplements import add_supplements
from .tables import add_compounding_tables
from .validation import compare_reference, validate_document


def build_document(pages, act):
    settings = ACTS[act]
    lines = prepare_lines(pages, settings["stem"])
    document, raw_sections = extract_sections(lines, act)
    diagnostics = {
        "nodes": 0,
        "duplicate_ids": [],
        "sequence_gaps": [],
        "residual_markers": [],
    }
    for section, raw in zip(document["sections"], raw_sections):
        structure(section, raw["lines"], settings["stem"], diagnostics)
    add_supplements(document, pages, lines, act)
    compounding = add_compounding_tables(document, pages) if act == "BNSS" else None
    report = validate_document(document, raw_sections, pages, lines, act, diagnostics)
    return document, raw_sections, lines, compounding, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=PDF_DIR,
        help="Folder containing the three original PDFs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Folder for the JSON and citation files.",
    )
    parser.add_argument(
        "--interim-dir",
        type=Path,
        default=INTERIM_DIR,
        help="Folder for page extraction records.",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=REFERENCE,
        help="Delivered JSON used only for output comparison.",
    )
    parser.add_argument(
        "--act",
        choices=list(ACTS),
        help="Optional single-act test; default is all three.",
    )
    args = parser.parse_args()
    acts = [act for act in ACTS if not args.act or act == args.act]
    require(
        file_hash(args.reference) == REFERENCE_SHA256,
        "The reference JSON differs from the delivered version.",
    )

    # Validate all requested inputs before doing any extraction work.
    for act in acts:
        source = args.pdf_dir / (ACTS[act]["stem"] + ".pdf")
        require(
            file_hash(source) == ACTS[act]["sha256"],
            f"{source.name}: incomplete or different source PDF. Check source_pins.json.",
        )

    documents, citations, reports = [], [], []
    for act in acts:
        pages = acquire(args.pdf_dir, args.interim_dir, act)
        document, raw_sections, lines, compounding, report = build_document(pages, act)
        if act == "BNSS":
            audit = audit_schedule(
                args.pdf_dir / (ACTS[act]["stem"] + ".pdf"), document
            )
            require(
                len(audit) == 47
                and all(
                    not page["column_failures"] and not page["row_overlaps"]
                    for page in audit
                ),
                "BNSS First Schedule: independent table audit failed.",
            )
            report["independent_table_pages_checked"] = len(audit)
            write_json(args.interim_dir / "BNSS_table_audit.json", audit)
        citations.extend(
            citation_entries(
                document, raw_sections, lines, act, len(documents), compounding
            )
        )
        documents.append(document)
        reports.append(report)
        print(
            f'{act}: {len(pages)} pages, {len(document["sections"])} sections; content checks passed.'
        )

    data = {"documents": documents}
    # The reference is never used to generate or repair any output value.
    compare_reference(data, read_json(args.reference))
    citation_ids = [entry["citation_id"] for entry in citations]
    require(len(citation_ids) == len(set(citation_ids)), "Duplicate citation IDs.")
    report = {
        "schema_version": "source-json-with-row-ids-1",
        "extractor": f"PyMuPDF=={PYMUPDF_VERSION}",
        "sources": {act: ACTS[act] for act in acts},
        "documents": reports,
        "reference_match_excluding_row_ids": True,
        "canonical_data_sha256": data_hash(data),
        "citation_count": len(citations),
        "blocking_defects": [],
    }
    name = (
        "Bns_Bnss_Bsa_2023_final.json"
        if len(acts) == 3
        else ACTS[acts[0]]["stem"] + "_parsed.json"
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Publish after all checks pass. The final JSON is written last.
    write_json(args.output_dir / "validation_report.json", report)
    write_json(
        args.output_dir / "citations.json",
        {"canonical_data_sha256": data_hash(data), "entries": citations},
    )
    temporary = args.output_dir / (name + ".tmp")
    write_json(temporary, data)
    require(read_json(temporary) == data, "JSON roundtrip failed.")
    temporary.replace(args.output_dir / name)
    print(
        "Reference comparison passed: only the 50 Section 359 row_id fields are new."
        if "BNSS" in acts
        else "Reference comparison passed."
    )
    print(args.output_dir / name)


if __name__ == "__main__":
    main()
