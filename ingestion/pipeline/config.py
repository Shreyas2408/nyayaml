"""Paths and source details for these three PDF editions."""

from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]

# Put Bns_2023.pdf, Bnss_2023.pdf and Bsa_2023.pdf in this folder.
# Or pass --pdf-dir "C:\\your\\folder" when running the pipeline.
PDF_DIR = PROJECT / "data" / "raw"

# The combined JSON, citation map and validation report are saved here.
OUTPUT_DIR = PROJECT / "data" / "processed"
INTERIM_DIR = PROJECT / "data" / "interim"
REFERENCE = PROJECT / "reference" / "Bns_Bnss_Bsa_2023_final.json"
REFERENCE_SHA256 = "970fdfe0c7851137114ed42227310e290d5d085350d522f8ce0f05e0c81db0b7"
PYMUPDF_VERSION = "1.26.6"

ACTS = {
    "BNS": {
        "stem": "Bns_2023",
        "pages": 112,
        "first": 16,
        "last": 111,
        "sections": 358,
        "chapters": 20,
        "groups": 26,
        "bytes": 896392,
        "sha256": "ff92dcc72778944011807644b6033b1140ddbe6d7e9f82ac32fd419dae03aa86",
    },
    "BNSS": {
        "stem": "Bnss_2023",
        "pages": 279,
        "first": 16,
        "last": 172,
        "sections": 531,
        "chapters": 39,
        "groups": 24,
        "bytes": 2070968,
        "sha256": "8047fe1de6092e23240d059b45cab8d4d93a1b77fa5de7f91ec12ac662c56512",
    },
    "BSA": {
        "stem": "Bsa_2023",
        "pages": 54,
        "first": 10,
        "last": 51,
        "sections": 170,
        "chapters": 12,
        "groups": 10,
        "bytes": 525216,
        "sha256": "993882ad0ae7ded6ae8087351edd61caeb6faa1a2d6c8fff8c269a9cf149b9ac",
    },
}
