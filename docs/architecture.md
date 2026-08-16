nyayaml/
├── .github/workflows/ci.yml        # lint + test on push
├── services/
│   ├── api/          FastAPI  (routers → services → repositories)
│   ├── retrieval/    stub for now, real container
│   ├── evaluator/    stub for now
│   ├── ingestion/    one-shot job container
│   └── frontend/     Vite + React + TS
├── libs/common/      shared pydantic schemas, logging, config
├── data/{raw,processed,benchmarks}
├── monitoring/{prometheus.yml,grafana/}
├── docker-compose.yml / .dev.yml / .test.yml
├── Makefile   .env.example   README.md
└── docs/{architecture.md,decisions/}