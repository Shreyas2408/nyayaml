"""Small regression tests for the mistakes that matter in this corpus."""

import copy
import tempfile
import unittest
from pathlib import Path

import fitz
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from pipeline.chunker import MAX_TOKENS, split_text, token_count
from pipeline.common import read_json, require
from pipeline.config import REFERENCE
from pipeline.hierarchy import structure
from pipeline.parser import prepare_lines, table_word_lines
from pipeline.section_extractor import split_header
from pipeline.tables import schedule_page
from pipeline.validation import (
    compare_reference,
    first_difference,
    validate_children,
    without_row_ids,
)


def line(text, x=90, y=100, bold=False):
    return {
        "t": text,
        "p": 1,
        "x": x,
        "y": y,
        "bbox": [x, y, x + 300, y + 12],
        "spans": [{"text": text, "font": "Times-Bold" if bold else "Times-Roman"}],
    }


class ExtractionTests(unittest.TestCase):
    def test_number_dash_does_not_end_title(self):
        number, title, body = split_header(
            [line("255.—Printed title.—Body text.", bold=True)]
        )
        self.assertEqual(
            (number, title, body[0]["t"]), ("255", "Printed title.", "Body text.")
        )

    def test_folio_removed_only_in_bottom_region(self):
        pages = [
            {
                "p": 1,
                "w": 612,
                "h": 792,
                "lines": [
                    {"t": "1", "bbox": [90, 100, 96, 112], "spans": []},
                    {"t": "1", "bbox": [300, 735, 306, 747], "spans": []},
                ],
            }
        ]
        result = prepare_lines(pages, "Test")
        self.assertEqual([item["t"] for item in result], ["1"])

    def test_missing_folio_blocks(self):
        pages = [{"p": 1, "w": 612, "h": 792, "lines": []}]
        with self.assertRaises(ValueError):
            prepare_lines(pages, "Test")

    def test_cross_reference_is_not_a_subsection(self):
        text = "The reference to sub-sections\n(1) and (2) stays in this sentence."
        section = {"title": "Test.", "section_number": "1", "text": text}
        lines = [
            line("The reference to sub-sections", y=100),
            line("(1) and (2) stays in this sentence.", x=72, y=112.6),
        ]
        diagnostics = {
            "nodes": 0,
            "duplicate_ids": [],
            "sequence_gaps": [],
            "residual_markers": [],
        }
        structure(section, lines, "Bnss_2023", diagnostics)
        self.assertEqual(section["subsections"], [])
        self.assertEqual(section["text"], text)

    def test_table_uses_glyph_columns(self):
        # Exercise the real PDF -> character positions -> table path on a tiny layout.
        pdf = fitz.open()
        page = pdf.new_page(width=600, height=300)
        for x, y, text in [
            (72, 100, "Alpha"),
            (220, 100, "12(1)"),
            (380, 100, "First person"),
            (72, 145, "Beta"),
            (220, 145, "13"),
            (380, 145, "Second person"),
        ]:
            page.insert_text((x, y), text, fontsize=11, fontname="Times-Roman")
        words = table_word_lines(page)
        result = schedule_page({"p": 1, "word_lines": words}, [72, 220, 380], 80, 170)
        self.assertEqual(
            result["rows"],
            [["Alpha", "12(1)", "First person"], ["Beta", "13", "Second person"]],
        )
        pdf.close()


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = read_json(REFERENCE)

    def test_counts_and_hierarchy(self):
        self.assertEqual(
            [len(doc["sections"]) for doc in self.reference["documents"]],
            [358, 531, 170],
        )
        for document in self.reference["documents"]:
            for section in document["sections"]:
                validate_children(section)

    def test_bsa_21_keeps_its_cross_reference_text(self):
        section = self.reference["documents"][2]["sections"][20]
        self.assertIn("132", section["text"])
        self.assertNotEqual(len(section["text"]), 0)
        self.assertEqual(section["subsections"], [])

    def test_section_359_full_text_and_tables(self):
        section = self.reference["documents"][1]["sections"][358]
        self.assertEqual(len(section["text"]), 8153)
        self.assertEqual(
            [len(sub["tables"][0]["rows"]) for sub in section["subsections"][:2]],
            [37, 13],
        )
        self.assertEqual(
            [sub["id"] for sub in section["subsections"]],
            [f"({i})" for i in range(1, 10)],
        )
        self.assertEqual(
            [clause["id"] for clause in section["subsections"][3]["clauses"]],
            ["(a)", "(b)"],
        )

    def test_reference_comparison_detects_a_lost_word(self):
        changed = copy.deepcopy(self.reference)
        changed["documents"][0]["sections"][0]["text"] = ""
        with self.assertRaises(ValueError):
            compare_reference(changed, self.reference)

    def test_reference_comparison_allows_only_row_id_additions(self):
        changed = copy.deepcopy(self.reference)
        changed["documents"][1]["sections"][358]["subsections"][0]["tables"][0]["rows"][
            0
        ]["row_id"] = "test-row"
        compare_reference(changed, self.reference)
        changed["documents"][1]["sections"][358]["subsections"][0]["tables"][0]["rows"][
            0
        ]["offence"] = "changed"
        with self.assertRaises(ValueError):
            compare_reference(changed, self.reference)

    def test_row_swap_is_a_difference(self):
        changed = copy.deepcopy(self.reference)
        rows = changed["documents"][1]["schedules"][0]["parts"][0]["table"]["pages"][0][
            "rows"
        ]
        rows[0], rows[1] = rows[1], rows[0]
        self.assertTrue(first_difference(changed, self.reference))


class ChunkTests(unittest.TestCase):
    def setUp(self):
        # A small test tokenizer checks boundary handling. The full-corpus test
        # recorded in docs/TEST_RESULTS.md uses the real pinned BGE tokenizer.
        self.tokenizer = Tokenizer(
            WordLevel({"[UNK]": 0, "word": 1, "title": 2}, unk_token="[UNK]")
        )
        self.tokenizer.pre_tokenizer = Whitespace()

    def test_long_text_is_partitioned_without_loss(self):
        text = ("word " * 1300) + "\n  "
        spans = split_text(text, "title\n", self.tokenizer)
        self.assertGreater(len(spans), 1)
        self.assertEqual("".join(text[start:end] for start, end in spans), text)
        self.assertTrue(
            all(
                token_count(self.tokenizer, "title\n" + text[start:end]) <= MAX_TOKENS
                for start, end in spans
            )
        )

    def test_oversized_prefix_blocks(self):
        with self.assertRaises(ValueError):
            split_text("word", "title " * 600, self.tokenizer)

    def test_short_section_stays_whole(self):
        text = "word word\nword."
        self.assertEqual(split_text(text, "title\n", self.tokenizer), [(0, len(text))])


if __name__ == "__main__":
    unittest.main()
