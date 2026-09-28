Verification performed while preparing this package

The checks below distinguish reproduction of the delivered JSON from a fresh extraction of the source PDFs.

| Check | Result | Scope |
|---|---|---|
| Regression suite | 14 tests passed | Heading delimiters, folios, cross-reference handling, native PDF table geometry, source reference comparison, hierarchy and chunk boundaries |
| Fresh BNS extraction | 112 pages matched the preserved extraction exactly | Actual original BNS PDF, PyMuPDF 1.26.6 |
| BNS pipeline run | 358 sections; full-document reference match passed | PDF acquisition through final JSON, including its front matter, hierarchy and statement |
| Section reconstruction | All 1,059 section objects matched | Preserved native source extraction for all three documents; Section 359 tables compared separately |
| BSA complete document reconstruction | Reference match passed | Preserved page text and geometry, including its Schedule and statement |
| Section 359 tables | 37 + 13 rows; only row IDs added | Reconstructed from preserved native page text and geometry |
| BNSS forms | All 58 forms and restored rules matched | Preserved page extraction |
| Native table adapter | Test passed | A generated PDF fixture exercises actual glyph positions, columns and rows |
| Independent First Schedule audit | Included as a mandatory BNSS build gate | The original audit recorded 47 passing pages; it could not be rerun against the presently truncated BNSS copy |
| Reference comparison | Deliberate missing text and swapped rows rejected | JSON-value comparison allows only `row_id` additions |
| Source preflight | Truncated BNSS input rejected | No use of the reference JSON as a substitute source |
| Full-corpus smart chunking | 1,839 chunks; maximum 511 tokens | Actual pinned BGE tokenizer; input was the delivered JSON plus the 50 row IDs |
| Chunk coverage | All 1,059 section ranges accounted for | Ordinary text spans plus the two explicit table replacements |
| Table chunk coverage | All 517 rows accounted for | 50 Section 359 rows and 467 First Schedule page-grouped rows |
| Citation resolution | All chunk citation IDs and JSON pointers resolved | Includes parent/context pointers and source offsets |
| Python syntax | All files compile | Final packaged source |

Tokenizer: BAAI/bge-base-en-v1.5, revision `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`.

Tokenizer file SHA-256: `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66`.

Reference JSON SHA-256: `970fdfe0c7851137114ed42227310e290d5d085350d522f8ce0f05e0c81db0b7`.

**What has not been established**

A fresh end-to-end extraction from all three complete PDFs was not possible in the packaging environment. Its BNSS copy contained 461,824 bytes instead of 2,070,968; its BSA copy contained 328,192 bytes instead of 525,216. The package rejects those inputs. No missing PDF bytes or source text were reconstructed by guessing.

The production First Schedule path uses actual PDF character positions. Its code preserves the earlier extraction decisions and adds the independent audit, but fresh execution against the complete BNSS PDF remains a local acceptance step. No approximation of glyph positions is used by the shipped parser.

The full-corpus chunk test is a real chunker test on the delivered corpus; it is not evidence that a fresh three-PDF extraction succeeded. The original reference JSON is included only to verify output and is never used to fill or generate source content.

The setup instructions target Windows PowerShell. Execution checks were run with Python 3.12 in the available Linux runtime. The code uses pathlib and explicit UTF-8/LF output for portability, but a Windows run remains part of your local verification.

“Reference equality passed” means that the code reproduced the supplied reference with only the authorized row IDs added. It should not be described as a proof of 100% legal correctness. Source review and chunk-context correctness remain separate responsibilities.
