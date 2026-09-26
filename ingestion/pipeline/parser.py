"""Read PDFs once and keep the text and geometry used by the later steps."""

import copy
import re

import fitz

from .common import file_hash, normalize_line, require, write_json
from .config import ACTS, PYMUPDF_VERSION


def table_word_lines(page):
    """Keep actual glyph positions before any table words are joined."""
    result = []
    for block in page.get_text("rawdict", sort=False)["blocks"]:
        for line in block.get("lines", []):
            chars = [char for span in line["spans"] for char in span["chars"]]
            text = "".join(char["c"] for char in chars)
            words = [
                {"text": match.group(), "x": chars[match.start()]["bbox"][0]}
                for match in re.finditer(r"\S+", text)
            ]
            if words:
                result.append({"y": line["bbox"][1], "words": words})
    return result


def extract_pages(pdf_path, act):
    settings = ACTS[act]
    require(
        fitz.VersionBind == PYMUPDF_VERSION,
        f"Install PyMuPDF=={PYMUPDF_VERSION}; found {fitz.VersionBind}.",
    )
    require(
        file_hash(pdf_path) == settings["sha256"],
        f"{pdf_path.name}: source hash differs. Use the complete original PDF listed in source_pins.json.",
    )
    require(
        pdf_path.stat().st_size == settings["bytes"],
        f"{pdf_path.name}: source size differs.",
    )
    pages = []
    with fitz.open(pdf_path) as pdf:
        require(len(pdf) == settings["pages"], f"{act}: unexpected page count.")
        for number, page in enumerate(pdf, 1):
            lines = []
            for block in page.get_text("dict", sort=False)["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    if not text:
                        continue
                    spans = [
                        {
                            key: span[key]
                            for key in ["text", "font", "size", "bbox", "flags"]
                        }
                        for span in line["spans"]
                    ]
                    lines.append(
                        {"t": text, "bbox": list(line["bbox"]), "spans": spans}
                    )
            require(
                bool(lines),
                f"{act} page {number}: no text layer. Review the source before continuing.",
            )
            text = "\n".join(line["t"] for line in lines)
            require(
                "(cid:" not in text and "\ufffd" not in text and "\x00" not in text,
                f"{act} page {number}: unresolved characters in the text layer.",
            )
            record = {
                "p": number,
                "w": page.rect.width,
                "h": page.rect.height,
                "lines": lines,
            }
            if act == "BNSS" and 173 <= number <= 219:
                record["word_lines"] = table_word_lines(page)
            pages.append(record)
    return pages


def prepare_lines(pages, stem):
    """Remove printed folios and join adjacent fragments on one baseline."""
    result = []
    for page in copy.deepcopy(pages):
        lines = []
        folios = 0
        for index, line in enumerate(page["lines"]):
            line.update(
                p=page["p"],
                uid=f'{stem}:{page["p"]}:{index}',
                x=line["bbox"][0],
                y=line["bbox"][1],
                w=page["w"],
            )
            if line["t"] == str(page["p"]) and line["y"] > page["h"] - 80:
                folios += 1
                continue
            lines.append(line)
        require(
            folios == 1,
            f'{stem} page {page["p"]}: printed folio is not uniquely identified.',
        )
        merged = []
        for line in lines:
            if (
                merged
                and abs(merged[-1]["y"] - line["y"]) < 1.5
                and line["x"] >= merged[-1]["bbox"][2] - 1
            ):
                previous = merged[-1]
                previous["t"] += " " + line["t"]
                previous["spans"] += line["spans"]
                previous["bbox"][2:] = line["bbox"][2:]
                previous["uids"].append(line["uid"])
            else:
                line["uids"] = [line["uid"]]
                merged.append(line)
        for line in merged:
            line["t"] = normalize_line(line["t"])
        result.extend(merged)
    return result


def acquire(pdf_dir, interim_dir, act):
    settings = ACTS[act]
    pages = extract_pages(pdf_dir / (settings["stem"] + ".pdf"), act)
    write_json(interim_dir / (settings["stem"] + ".pages.json"), pages)
    return pages
