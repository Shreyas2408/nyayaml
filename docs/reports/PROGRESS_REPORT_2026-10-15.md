# NyayaML v3.1: Progress Report & Technical Review
*Date: 2026-10-15 · Baseline: `nyayaml_master_plan.md` (Aug 8, 2026) · Last pushed commit: `45dc2e4` (2026-09-28)*

## 1. Executive summary
- **Phase 1 (ingestion) is done in function:** 1,059 sections → 2,322 chunks → 768-d vectors in Qdrant plus a BM25 index. Every step is checked by hash against the one before it.
- **Hybrid retrieval works end to end from the command line.** The verified run is in §2.
- **Schedule:** we're in week 10 of 13. The plan expected Phases 6–7 by now; actual progress is the start of Phase 2, about 6 weeks behind (see §6).
- **Biggest risk:** none of the work since 09-28 has been committed (chunker rewrite, embedder, loader, BM25, retrieval layer).
- **Top 3 actions:** (1) commit, (2) fix hybrid candidate depth, (3) retrieval tests, then the Swagger demo.

## 2. Verified result: hybrid retrieval
Query: `"compounding of offences" --act BNSS --top-k 3`, run from repo root as `python -m backend.app.retrieval.hybrid`.

| Rank | Hit | Sources | RRF score | Check (k=60) |
|---|---|---|---|---|
| 1 | BNSS §359(8) | dense + sparse | 0.0325225 | 1/61 + 1/62 ✅ |
| 2 | BNSS §359(7) | dense | 0.0163934 | 1/61 ✅ |
| 3 | BNSS §359(1) | sparse | 0.0161290 | 1/62 ✅ |

- **What's right:** the act filter works, every result is the correct section with verbatim text, the `citation_id` and `json_pointer` resolve, and the RRF math is exact.
- **Issue (High): candidate depth equals `top_k`.** With `--top-k 3`, each side fetches only 3 candidates, so fusion ranks 6 items. The master plan specifies **top-20 per side → RRF → return top_k**. Shallow lists make RRF unstable: §359(7), dense's #1 hit, falls below a hit both sides merely agreed on.
- **Issue (Medium): `source_pages` is per section, not per chunk.** Every §359 sub-chunk reports pages 123–127. The Phase 5 evidence viewer needs page ranges per chunk. First check whether the data allows this (offsets → page map).

## 3. Environment & infrastructure
**grpc DLL block.** The error "An Application Control policy has blocked this file" comes from Windows Smart App Control blocking `cygrpc.pyd` (grpcio 1.83.0).
- **Evidence:** `cygrpc.pyd` is the oldest file in the chain. It predates chunks, embeddings and the loader runs that succeeded with it. So the file didn't change; Windows' policy did.
- **Status:** resolved. The hybrid run imports `qdrant_client` fine.
- **Action:** write down what fixed it (Smart App Control turned off, or a reinstall) in `docs/` so the setup can be reproduced.

**Docker.**
- **Correct and running:** the compose project `nyayaml` → `nyayaml-qdrant-1`, on port 6333 with named volume `qdrant_storage`. For now this is the only container needed.
- **Leftover:** the stopped `nyayaml-qdrant` came from an earlier `docker run` that duplicated the compose service. It uses the bind folder `./qdrant_storage/`. Remove it with `docker rm nyayaml-qdrant`. After `curl.exe http://localhost:6333/aliases` confirms the alias, delete the orphaned `qdrant_storage/` folder.
- ⚠️ **Don't run `make up` or `docker compose up -d` for the full stack.** The backend image is a month old (from Phase 0), `main.py` is fully commented out (so it would crash-loop), the Dockerfile copies only `backend/` (no `ingestion` package), and its requirements lack sentence-transformers and rank-bm25.
- ⚠️ **Never run `make down`.** It runs `docker compose down -v`, which deletes the Qdrant volume and with it the whole vector index.
- **Builds tab:** Phase 0 images only. The two incomplete backend builds (35m and 18m) don't matter now.

**Dependency drift.**
- `backend/requirements.txt` pins qdrant-client 1.12.1, pydantic 2.10.4 and fastapi 0.115.6. The venv has qdrant-client 1.19.0 and pydantic 2.13.4, and **no FastAPI**.
- `chromadb 1.5.9` is installed but nothing in the code uses it. Uninstall it.

## 4. Code review: retrieval layer
**All imports resolve.** The modules use the `ingestion.pipeline.*` import root, so they must run from the repo root as `python -m backend.app.retrieval.<module>`.

| File | Verdict | Findings (severity) |
|---|---|---|
| `ingestion/pipeline/common.py` | ✅ | `read_jsonl` is correct. PEP 8 blank-line nit (Low) |
| `artifacts.py` | ✅ Strong | Cross-checks 4 reports and 2,322 records, failing closed. The duplicated constants are deliberate: importing them would pull in qdrant_client/torch, and drift is caught at runtime. `parents[3]` is only valid in the repo layout, not inside the Docker image (Medium, Phase 6). No module docstring (Low) |
| `dense.py` | ✅ Strong | Correct BGE query prefix. The alias is re-checked after each query. The lock wraps `encode`, so concurrent API queries run one at a time; narrow it to the lazy model load (Medium, Phase 4). The first search verifies all vectors (slow once). No docstring, so `--help` shows no description (Low) |
| `sparse.py` | ✅ | Every chunk record must equal `chunks.jsonl` exactly. The BM25 build is tied to the dense build ID |
| `fusion.py` | ✅ | RRF math is confirmed by the run. Suggest recording per-source ranks for debugging and evaluation (Low) |
| `hybrid.py` | ✅ Works | Candidate depth equals top_k (**High**, see §2). `standard()` builds `CorpusArtifacts` twice; share one instance (Low). Results lack `chunk_id` (Low). No docstring (Low). The `require` import is present ✅ |

**Cross-cutting issues.**
1. **Package root is undecided.** The retrieval code uses `backend.app.*` and `ingestion.pipeline.*`, while the Phase 0 scaffold, the Dockerfile and CI assume `app.*` run from `backend/`. Decide before Phase 4. Recommended: package `ingestion` with a small `pyproject.toml` and `pip install -e ingestion`, then run the backend from `backend/` as `app.*`. That works the same locally, in Docker and in CI.
2. **`backend/tests/conftest.py` blocks pytest.** It imports `app.main.app` and the DB at import time, so pytest collection fails for every backend test. Use `unittest` for the retrieval tests for now, the same as the ingestion tests.
3. **Tests.** There are no retrieval tests: `test_dense_sparse.py` was never saved. The 14 ingestion tests were written against Astra's chunker; re-run them now that the chunker has been rewritten.

## 5. Repository tree (current, condensed)
```
nyayaml/
├── .github/workflows/        ci.yml (ruff lint, py3.11), benchmark.yml
├── backend/
│   ├── Dockerfile, requirements*.txt, alembic/ (0001_initial)
│   ├── app/
│   │   ├── main.py                 ⚠ fully commented out
│   │   ├── retrieval/              artifacts ✅ dense ✅ sparse ✅ fusion ✅ hybrid ✅ | reranker, compressor = stubs | router.py ✗
│   │   ├── query/ llm/ verification/ evaluation/ prompts/ sections/ analytics/ auth/ core/ db/   (Phase 0 scaffolds)
│   └── tests/                      conftest.py ⚠, test_*/ (empty)
├── ingestion/
│   ├── pipeline/   parser, section_extractor, hierarchy, tables, supplements, audit_tables, metadata,
│   │               validation, run_pipeline, chunker, download_tokenizer, embedder, qdrant_loader,
│   │               bm25_builder, common, config | models.py, landmarks.py = dead code
│   ├── tests/test_pipeline.py (14), source_pins.json, requirements.txt, Dockerfile
├── data/
│   ├── raw/ (3 PDFs, pinned)   interim/ (pages, audits)   tokenizer/tokenizer.json
│   ├── processed/  Bns_Bnss_Bsa_2023_final.json, citations, validation_report,
│   │               chunks.jsonl, chunk_report, embeddings.npy, embedding_report, qdrant_load_report
│   │               (+ stale single-act: Bns_2023_parsed, BSA_2023_parsed, bnss_sections)
│   ├── bm25_index/ current.json → builds/8bc2357e5483a88f/{index.json, bm25_report.json}
│   └── benchmarks/questions_v1.json, embeddings/ (empty)
├── reference/Bns_Bnss_Bsa_2023_final.json (sha 970fdfe0…)
├── docs/ architecture, system-design, api-spec, ingestion/{EXTRACTION_STEPS, PLAN_CHANGES, TEST_RESULTS}, reports/
├── frontend/ (package.json, tsconfig, Dockerfile only)   monitoring/ (prometheus, 3 dashboards)   nginx/
├── qdrant_storage/  ⚠ orphaned (from the standalone container)
└── docker-compose{,.dev,.test}.yml, Makefile, .env.example, .gitignore, .dvcignore, README.md
```

## 6. Progress against the master plan
| Phase (planned weeks) | Status | Done | Open |
|---|---|---|---|
| 0 Foundation (1) | ~85% | Structure, compose file with 7 services, Makefile, CI lint, docs | Backend doesn't boot right now; CI uses py3.11 but local is 3.10 |
| 1 Ingestion (2–3) | **~85%** | Parser, 1,059 sections, metadata, smart chunking (2,322 chunks, ≤479 tokens, §359 rows kept whole), BGE embeddings (CPU), Qdrant, BM25, idempotent reruns, 14 tests | **DVC** ✗; tests for the loader, embedder and BM25 ✗. *Deliberate deviation:* one aliased collection with an `act` index instead of 3 collections |
| 2 RAG engine (3–5) | ~15% | Dense, sparse and RRF hybrid (command line) | API demo, reranker, compression, analyzer/expander/orchestrator, LLM, verification, structured JSON, multi-turn, tests |
| 3 Evaluation + CI (5–6) | ~5% | `questions_v1.json` exists | Everything else |
| 4 API + Auth (6–7) | ~5% | Scaffolds | Everything else |
| 5 Frontend (7–9) | 0% | — | Everything |
| 6 DevOps / DVC (9–11) | ~10% | Compose files, Makefile, CI lint | CD, DVC, GHCR |
| 7 Monitoring (11–12) | ~5% | Dashboard JSON scaffolds | Metrics, alerts |
| 8 Docs + demo (12–13) | ~10% | Ingestion docs | Report, demo script |

**Schedule:** about 6 weeks behind, with roughly 3 weeks left. Phase 1 was built to a much higher standard than planned (certified corpus, hash chain), and that cost time but is now a strength.

**Recommended re-baseline (MVP cut):**
1. The Phase 2 core: reranker → Ollama LLM → citation and hallucination checks → structured JSON.
2. The Phase 3 evaluation harness on the existing questions.
3. A minimal Phase 4 API.

Defer these to after the demo: auth, the full React UI, the Grafana alerts, the DVC remote.

## 7. Risk register
| Risk | Impact | Mitigation |
|---|---|---|
| Uncommitted work since 09-28 | Total loss of Phase 1 + retrieval | Commit today, staging explicit paths only (not `git add .`) |
| `make down -v` | Deletes the vector index | Remove `-v` or never run it; the index can be rebuilt with the loader |
| `.gitignore` misses `*.jsonl`, `*.npy`, `tokenizer/`, `bm25_index/builds/`, `qdrant_storage/` | Large binaries pushed to GitHub | Stage explicit paths; add the patterns before Phase 6 |
| Package root undecided | Docker and CI breakage in Phase 4/6 | Editable `ingestion` package |
| Windows Smart App Control | Native DLLs blocked again | Document the fix; move the dev environment to Docker/WSL in Phase 6 |

## 8. Next actions (in order)
1. **Commit and push.** Check `git status` first, then stage explicit paths: `ingestion/pipeline/*.py`, `ingestion/requirements.txt`, `backend/app/retrieval/*.py`, `docs/`.
2. **Fix `hybrid.py`.**
   - Set `CANDIDATE_DEPTH = 20`, so each side fetches `max(top_k, 20)` and the fused list is cut to `top_k`.
   - Build one shared `CorpusArtifacts` instance and pass it to both retrievers.
   - Add a module docstring, and add `chunk_id` and per-source ranks to each result.
3. **Add `backend/tests/test_retrieval/test_hybrid.py`, run with `unittest`.** It covers the RRF math (pure, no network), hybrid with fake retrievers, and three golden queries:
   - "punishment for murder" → BNS §103/§104
   - "compounding of offences" with BNSS → BNSS §359
   - one BSA evidence question

   These become the seed for the Phase 3 evaluation.
4. **Swagger demo.**
   - Install FastAPI pinned to a version compatible with pydantic 2.13.
   - Add `backend/app/retrieval/router.py` and a minimal `main.py` using relative imports.
   - From the repo root, run `python -m uvicorn backend.app.main:app --port 8000` and open `/docs`.
5. **Docker cleanup.** `docker rm nyayaml-qdrant`, confirm the alias over REST, then delete `qdrant_storage/`. Also `pip uninstall chromadb`.
6. **Phase 2 build-out.** A cross-encoder (ms-marco-MiniLM-L6-v2, CPU) reranks the top 40, then compression, the Ollama client with fallback, and citation verification.
7. **Tech debt before Phase 4.** The editable `ingestion` package, syncing `backend/requirements.txt`, a lazy `conftest.py`, and DVC.

## Appendix: not yet verified
These could not be checked when this report was written; confirm them yourself:
- `git status`: what is uncommitted
- the Smart App Control state
- `docker ps`
- `curl.exe http://localhost:6333/aliases`
