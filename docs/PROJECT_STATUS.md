# NyayaML — Project Status & Engineering Handbook
**One-liner:** Citation-grounded legal RAG over India's 2023 criminal codes (BNS/BNSS/BSA) — hybrid retrieval, LLM answers with verified citations, authenticated API, append-only audit trail.
**Status:** Phase 3/4 COMPLETE. 9/9 packages committed, pushed, CI-green. Tag: `phase-3-4`.

---

## 1. What It Is
A modular-monolith legal question-answering system. Corpus: 1,059 sections (BNS 358 / BNSS 531 / BSA 170), 2023 statutory snapshot. Pipeline: query analysis → hybrid retrieval (BM25 + dense BGE-base + MiniLM cross-encoder rerank) → LLM generation (Ollama, structured output) → citation verification (fail-closed). Every query is audited durably per-query.

## 2. Stack & Services
| Layer | Tech |
|---|---|
| API | FastAPI (uvicorn), async SQLAlchemy, Pydantic |
| DB / cache / vector | PostgreSQL (Alembic 0003), Redis (Lua rate limits), Qdrant |
| LLM | Ollama (CPU-bound; GPU available but unused) |
| Tests / lint | unittest only (never pytest), Ruff pinned 0.8.6 |
| CI | GitHub Actions `ci` — offline, Python 3.10, ruff + full suite |
| Env | Windows 11 + Docker Desktop + WSL2 (10GB), host `.venv` (py3.10.11) |

## 3. Phase 3/4 Package Ledger
| Commit | Package | Delivered |
|---|---|---|
| c1dbdc6 | P34-01 | QueryOrchestrator wired, typed responses, citation fail-closed |
| 1c89820 | P34-02 | 32-question dataset (21 validated / 11 quarantined), SHA-pinned manifest |
| bf15f3c | P34-03 | Evaluation harness: P/R@K, hallucination proxy, refusal accuracy, R-7 latency |
| baa3977 | P34-04 | query_jobs append-only audit table (Alembic 0002), per-query commits |
| 50b3243 | P34-05 | JWT auth: register/login/refresh/logout, atomic rotation, bcrypt-12 |
| 54843b7 | P34-06 | POST /api/v1/query + history — auth, 30/h quota, owner isolation |
| 1ae6185 | P34-07 | Sections lookup (byte-exact) + owner analytics; compose override committed |
| 65e1f58 | P34-08 | Offline CI + host release gate (min recall@5 = 0.807); E402 fix |
| 47f9ffc | P34-09 | docs/reports/PHASE_3_4_ACCEPTANCE.md — the master evidence document |

## 4. Verified Numbers (proxies, not legal accuracy)
- Retrieval (21 validated, deterministic ×3): recall@1 **0.476** / @3 0.810 / @5 **0.857** / @10 0.905
- Candidates (11, QUARANTINED): recall@5 0.500 — never headline
- With-LLM: hallucination proxy **9.5% (2/21, watchlist)**; refusal accuracy **0.000 (0/5 — known gap)**; p50 ~70s (CPU)
- Tests: 258 host (1 opt-in skip) / 254 CI (18 data-gated skips, catalogued)
- Live-verified: 1,059 sections byte-exact; quotas 30/31 & 120/121; token rotation 1×200/1×401; replay 401; owner isolation

## 5. Security Model
JWT HS256 (access 30min, refresh 7d, type+jti claims); bcrypt cost 12; refresh tokens stored as SHA-256 digests (72-byte bcrypt limit); atomic rotation via conditional UPDATE; idempotent logout; per-endpoint Redis rate limits (fail-closed 503); owner-scoped queries only; audit rows never deleted.

## 6. Hard-Won Operational Rules
1. `.env` changes require `docker compose up -d --force-recreate <svc>` — containers don't reload env.
2. Backend source/cache mounts live in **committed** `docker-compose.override.yml` — recreating is safe now.
3. Lint with **pinned** ruff (`.venv-phase34`), not the dev venv's 0.16.1 — they disagree.
4. Windows-native postgres service can hijack port 5432 — stop it if DB auth breaks.
5. Native `ollama.exe` steals :11434 — Docker ollama only.
6. unittest + host venv for tests; never pytest; never `git add .`; named-path staging only.
7. Never `down -v`; never re-ingest/re-embed; frozen pipeline files change only via scoped exceptions.
8. `git fetch --prune origin` before any local/remote comparison.

## 7. Known Limitations (stated, not hidden)
- No refusal/out-of-scope gate (accuracy 0.000) — top priority hardening package
- LLM CPU-bound; 3 timeouts in benchmark; RTX 3050 unused in Docker
- `corpus_sha256` column exists but NULL in all query_jobs rows
- Local corpus bytes differ from pinned reference (drift; baselines remain self-consistent)
- Access tokens valid ≤30min post-logout; no account lockout / email verification (accepted v1)
- `.venv/pyvenv.cfg` references a moved Python install — venv works; do not rebuild

## 8. Backlog (post-Phase 3/4)
1. Refusal-gate hardening package (own eval cycle)
2. GPU offload for Ollama (compose `deploy.resources`)
3. Populate corpus_sha256 in audit writes
4. Resolve 19 legacy dirty files (commit / stash / discard — owner decision)

## 9. Key Commands
- Full suite: `.venv/Scripts/python.exe -m unittest discover -s backend/tests -t . -v`
- Pinned lint: `.venv-phase34/Scripts/ruff.exe check backend scripts`
- Release gate: `.venv/Scripts/python.exe scripts/release_gate.py` (expect recall@5 = 0.857, GATE PASS)
- API: `localhost:8000/docs`

*Full evidence: docs/reports/PHASE_3_4_ACCEPTANCE.md (committed, tag phase-3-4).*
