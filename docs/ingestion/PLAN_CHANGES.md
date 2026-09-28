Changes to nyayaml_master_plan.md — through smart chunking only

My rating is **8.5/10 for using this structured JSON as the canonical source dataset**. That is a design judgment, not a measured chatbot accuracy score. Complete section bodies plus their structure, source tables, footnotes and schedules are a useful basis for retrieval. The remaining requirements are reproducible extraction, source-version citations, measurable validation and a separate chunk layer.

Your application architecture and everything after smart chunking are outside this review. The original master-plan file has not been modified.

| Where in your plan | Required change |
|---|---|
| Phase 0, ingestion folder | Add `data/interim`, `reference`, `source_pins.json` and the extraction/citation validation files. Keep source PDFs immutable. |
| Phase 0, local setup | Make the ingestion script runnable directly in a Python environment. The extraction demonstration should not require the application containers to be running. |
| Phase 1, act census | Replace approximate counts with this snapshot's measured counts: BNS 112 pages/358 sections; BNSS 279/531; BSA 54/170; total 445/1,059. |
| “PDF/Text Parser” | Pin the exact PDF hashes and extractor version. Preserve native text, font spans and coordinates. Identify front matter, body, footnotes, schedules and statements before building section objects. |
| Parser cleanup | Remove only confirmed folios and normalize layout whitespace. Retain meaningful headings, printed punctuation, form rules and table column-number headers. An unresolved glyph or missing text layer blocks this source profile. |
| “Section Extractor” | Keep the complete source body, chapter/part hierarchy, nested provisions, residual text, footnotes and tables. Section boundaries must survive page breaks. |
| `{act, section_number, title, text, chapter, punishment_clause}` | Replace this proposed minimal schema with the delivered source schema. Keep `act`, hashes, page locations and generated provenance in a citation/manifest sidecar. Do not infer a `punishment_clause` during source extraction. |
| Heading association | Use both printed body headings and the source contents. Preserve explicit subheading positions. BNSS Chapter V is supported by its contents; do not invent a missing printed body heading. |
| Tables | Add dedicated geometry-based processing for BNSS Section 359 and the First Schedule. Check rows and columns, not merely word totals. Keep all 58 statutory forms and both other acts' supplementary material. |
| Citation preparation | Add version-scoped `row_id` values to Section 359's 50 row objects. Keep First Schedule row IDs in the citation sidecar because its existing rows are cell arrays. |
| Before “Smart Chunking” | Add a hard validation gate: source hashes, ordered section census, body conservation, valid hierarchy, table completeness/alignment, reference comparison and resolvable citations. |
| “Keep section boundaries intact” | Keep complete sections intact in the canonical JSON. Permit long sections to produce several linked retrieval chunks. Do not send an oversized complete section to a 512-token model and silently truncate it. |
| Chunk size | Count the complete embedding input with the pinned BGE tokenizer, including source headings, repeated context and special tokens. Enforce at most 512 tokens. |
| Chunk content | Read the section body once; avoid embedding full parent text and every repeated child text as independent copies. Preserve exact source slices. |
| Table chunks | Emit one row per retrieval unit when it fits. Include its source column labels and relevant introduction. Oversized rows retain a shared parent row citation across linked parts. |
| Qualification handling | Keep references to the relevant parent and qualifications. Section 359 rows explicitly link to subsections (3)–(9). The later evidence assembly must resolve these references. |
| Versioning/deliverable count | Track source JSON, citation map, validation report, tokenizer revision and chunking configuration separately. Section count remains 1,059; chunk count is a different measured quantity. |

The replacement ingestion sequence is:

1. **Source inventory:** exact filenames, byte sizes, hashes, page counts and section census.
2. **Native extraction:** text, spans and coordinates from every page, with no legal rewriting.
3. **Region identification:** front matter, operative body, footnotes, schedules, forms and statements.
4. **Section and heading parsing:** complete bodies, titles, chapter/part mapping and printed heading order.
5. **Hierarchy parsing:** subsections, clauses, deeper nodes, introductions and residual material, with conservation checks.
6. **Tables and supplements:** Section 359 rows, First Schedule cells, all forms, certificates, notes and statements.
7. **Citation metadata:** versioned IDs, JSON pointers and source pages; the main JSON receives only the authorized `row_id` additions.
8. **Validation gate:** compare the extracted result with the delivered reference, excluding only row IDs; independently audit First Schedule cell order and row positions.
9. **Smart chunking:** derive a separate token-bounded retrieval dataset and check coverage, parent links, table rows, citation resolution and actual token lengths.

**Acceptance checks before moving beyond chunking**

- The three source hashes match the approved snapshot.
- All 445 pages are processed and all 1,059 section numbers occur once, in order.
- Every section body is conserved during structure parsing.
- All 71 chapter assignments are retained, with the source's 70 printed body chapter headings and its contents-supported BNSS Chapter V distinguished.
- All 60 body subheadings retain their source positions.
- Section 359 has 37 + 13 rows, correct cell relationships, standard subsection fields and complete body text.
- The First Schedule has all 47 pages, with an independent alignment audit.
- All 58 forms, three commencement footnotes, schedules and other source material remain in the canonical JSON.
- Removing `row_id` from the generated JSON yields exact JSON-value equality with the delivered reference; wording, punctuation and arrays are not normalized for this comparison.
- Citation IDs are unique within the versioned dataset and resolve to source entities.
- Every chunk's actual embedding input fits the tokenizer limit without truncation.
- All source section ranges are accounted for by ordinary chunks or their explicit table replacements.
- All 517 table rows are represented; all emitted string partitions preserve their characters.
- Front matter and statements remain in the source dataset but are explicitly outside the default operative-content chunk set.

**Two distinctions to put in the plan**

“Same output” means reproduction of the delivered source artifact plus the requested row IDs. It does not prove that the reference itself is free of every possible semantic or structural error. Keep the source checks and independent review as separate acceptance evidence.

“Citation row 1” is your generated locator. It is not a new legal subsection or a row number printed by the Gazette. A row identifier must be used with its act, section/subsection, table and source snapshot.

The tokenizer limit is supported by the model's official [configuration](https://huggingface.co/BAAI/bge-base-en-v1.5/raw/main/config.json). PyMuPDF's [text-extraction documentation](https://pymupdf.readthedocs.io/en/latest/app1.html) explains the span/character information and why the PDF's native text order can differ from visual reading order.
