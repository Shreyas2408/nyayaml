Local reproduction: implementation and verification sequence

The target is the delivered source JSON with 50 `row_id` fields added to Section 359's existing row objects. All other JSON values must remain equal. The code is specific to these three approved PDF files; it is not a general parser for arbitrary scans or editions.

1. **Check the input bytes.** `config.py` and `source_pins.json` identify each original by filename, exact size and SHA-256. `run_pipeline.py` checks all selected inputs before extracting any of them. This catches the truncated PDF problem before a partial text layer can produce plausible-looking output.

2. **Read the native text layer.** `parser.py` uses PyMuPDF 1.26.6. It stores page dimensions, native line order, each line's text and bounding box, and span text/font/size/flags. For the First Schedule it additionally records actual word starts from the PDF's raw character geometry. It does not estimate word positions from font widths.

3. **Prepare lines conservatively.** A printed folio is removed only when it equals the page number and lies in the bottom region. Adjacent fragments on the same baseline are joined if they continue left-to-right. Spaces and tabs are normalized without rewriting punctuation or words. The page extraction records remain available in `data/interim`.

4. **Locate the source regions.** The operative body page ranges are BNS 16–111, BNSS 16–172 and BSA 10–51. Earlier material is retained as front matter. Later schedules and statements are processed separately. This keeps contents entries from becoming duplicate sections.

5. **Find real section headings.** A section heading needs both a numbered prefix and a bold first meaningful span. Whitespace-only spans are skipped. The title may wrap across lines; its printed dash separates it from the body. The special punctuation immediately after BNS Section 255's number is handled without treating it as the end of the title.

6. **Preserve section continuity.** Lines accumulate until the next section or structural heading. A new page does not create a new section. The section's `text` starts after its heading and includes its entire body.

7. **Build chapter and heading metadata.** Chapters are associated using the source contents and body. The contents-column label `SECTIONS.` is excluded from chapter titles. BSA Part information is retained. The `body_structure` event sequence records printed chapters, parts, subheadings and section references. It does not fabricate a body heading for BNSS Chapter V.

8. **Parse the provision hierarchy.** `hierarchy.py` combines labels, indentation, paragraph spacing and sibling sequences. It handles a marker printed on the same line as its parent, distinguishes wrapped cross-references from new nodes, and disambiguates alphabetic `(i)` from a Roman-numeral child.

9. **Keep residual material.** Explanations, illustrations, exceptions, provisos and closing paragraphs remain attached to their source level. Numbered examples are not automatically treated as operative subsections. The internal event tree reconstructs the section body; its non-whitespace characters must match the extracted section text.

10. **Extract the supplementary content.** Footnotes remain attached to Section 1. Schedules, BSA's certificate, the statements and all 58 BNSS forms are retained. Printed form rules and continuation column-number headers remain present. No later validation report is needed as a runtime input.

11. **Reconstruct the First Schedule.** `tables.py` uses the measured column starts appropriate to each page and the actual PDF word coordinates. It preserves wrapped cells and empty cells. Part I has six columns across 46 pages; Part II has four columns on one page. The result remains in the existing cell-array schema.

12. **Reconstruct Section 359's two tables.** The source regions on pages 123–126 contain 37 and 13 rows. The section-reference column provides row anchors, with separate handling for two lines whose native text spans adjacent columns. Printed headings and column labels are extracted. The complete section body and nine subsections remain unchanged.

13. **Assign the authorized row metadata.** The only new main-JSON key is `row_id` on each of those 50 row objects. It incorporates the source hash prefix, act, section, subsection, table and row ordinal. Citation metadata for all sections, these rows, First Schedule rows, forms and schedule notes is saved separately.

14. **Validate content and reproduce the reference.** Checks cover page/section/chapter/subheading counts, ordered numbering, full section-body conservation, child containment, duplicate labels, sequence gaps, form counts, table widths and character coverage. The completed output is then compared with the included reference, allowing only row-ID additions. A reference mismatch raises an error; no reference text is copied into the result.

15. **Audit table geometry independently.** Before publishing a full BNSS build, `audit_tables.py` uses pdfplumber 0.11.8 on all 47 First Schedule pages. It assigns characters to columns before joining words, compares column text sequences and checks row vertical bounds. This guards against a word-total match concealing a row or column mix-up.

16. **Publish successful output.** The manifest records the source pins, extractor, checks and canonical data digest. The citation map is tied to that same digest. The combined JSON is written through a temporary file after the gates pass. There are no timestamps inside the canonical legal data.

17. **Create a separate retrieval view.** `chunker.py` requires the matching source JSON, citation map and validation report. The official pinned BGE tokenizer counts the entire embedding input. Short sections stay together; long ones use hierarchy/paragraph boundaries and, where necessary, exact source slices based on token offsets. All slices remain linked to their parent.

18. **Handle tables and qualifications in chunks.** Explicit table regions are replaced by row chunks, so the full linearized table is not also indexed as ordinary section text. Column labels identify the values. Section 359 rows include their introduction and references to its subsequent conditions. First Schedule rows link to the Schedule and its notes. These context links must be resolved by the later evidence-assembly stage.

19. **Verify chunks separately from extraction.** Check the token ceiling, exact text partitions, section-range coverage, table-row count, unique chunk IDs, source pointers and citation resolution. The current full-corpus chunking test produced 1,839 chunks with a maximum of 511 tokens; that result uses the delivered reference plus row IDs as its input and does not imply that a fresh three-PDF extraction was executed here.

The original JSON includes deliberate overlapping views: complete section `text`, complete child `text`, introductions and residual fields. The source dataset retains these views for browsing and structured access. The chunker avoids counting them all as separate copies of the same content.

For this profile, successful reference equality is a strict reproduction test, including punctuation and string line breaks. Source-body conservation is a different test and ignores whitespace only while comparing a parsed body against its extracted lines. Neither test by itself establishes universal legal correctness or current-law status outside the supplied PDF snapshot.
