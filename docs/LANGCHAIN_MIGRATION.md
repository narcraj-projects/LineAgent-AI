# LangChain pipeline migration

The model names/results below describe the migration stage. The subsequent
accuracy-first selection uses Nomic v1.5 embeddings and combined dashboard/API
defaults; see [current model selection](MODEL_SELECTION.md).

The Python router now runs LangChain document, ingestion, retrieval and generation
components. LangGraph is not installed or used. Model authority and database
durability remain constrained by the original application services.

## Implemented stages

| Stage | Actual LangChain component | Preserved boundary |
| --- | --- | --- |
| PDF load | LangChainPDFLoader / BaseLoader, yielding Document objects | The strict parser verifies the two PDFs and canonical text before exposing documents. |
| Section chunking | RecursiveCharacterTextSplitter, 2000 characters, zero overlap | Exactly one unchanged bounded section per physical PDF page; 30 sections, original IDs and logical/physical references. |
| Ingestion | AuditedVectorStore.add_documents and the Embeddings interface | The full canonical 80-document corpus and metadata must match before any embedding or index write. |
| Dense/hybrid retrieval | VectorStore.as_retriever().invoke | Validated Pinecone v10 documents queries; original BM25/dense weighted RRF; source/context checks and actual scores. |
| Exact/graph retrieval | Read-only StructuredTool.invoke | Python selects allowlisted operations; SQL audit and canonical graph validation remain authoritative. |
| Generation | ChatPromptTemplate -> ChatFireworks -> strict output runnable | Raw provider envelope checks before library normalization, then strict Pydantic parsing and exact approved-fact equality. |

These are custom LangChain adapters for the existing verified provider ports.
Using a generic Pinecone vector-store or unrestricted graph/SQL agent would
discard the application's v10 full-text, provenance and allowlist contracts.

Embedding preprocessing, model identity, vector normalization and index-dimension
validation remain in the audited Fireworks adapter behind the LangChain
Embeddings interface. LangChain does not authorize arbitrary PDF uploads,
choose a negative decoy, invent sources, or control equipment.

The original dataset, two synthetic PDFs, baseline work orders and previously
published evaluation files are unchanged. A manifest/corpus/model fingerprint
plus the -lc-v1 tag isolates the migration namespace from the original PDF
namespace. New feedback remains SQL evidence; no Pinecone score is fabricated.

## Packages and models

Pinned modular packages: langchain-core==1.6.12,
langchain-fireworks==1.7.1, langchain-text-splitters==1.1.3.
The umbrella langchain/LangGraph packages are not needed. The clean project
environment is .venv-langchain; unlike the older environment, it does not
inherit unrelated global packages. pip check reported no broken requirements.

Configured generation: accounts/fireworks/models/deepseek-v4p1-flash.
Configured embeddings: fireworks/qwen3-embedding-8b, with the observed exact
provider identity accounts/fireworks/models/qwen3-embedding-8b. The existing
1024-dimensional compatible index is preserved. No claim is made that these
models are best, fastest or cheapest; comparing models is a separate next step.

Automatic LangChain tracing is disabled for these calls. The evaluation runner
continues to own explicit synthetic, secret-free LangSmith uploads and usage
instrumentation. SDK retries are disabled for generation; failures are retained.

## Verification

Run commands and actual results are recorded in ignored evals/results/:

- Clean environment Python 3.13.5: 342 passed, 20 skipped, five upstream PyMuPDF warnings.
- Fresh-process offline imports and production type annotations: PASS within pytest.
- React: 10 passed; TypeScript and Vite build: PASS.
- Real local Edge browser: four cases passed, including live providers and Atlas; no feedback submitted.
- Live LangChain ingestion: 80 documents acknowledged and dense/full-text visibility verified.
- Exact-token gates: four hybrid passes and four dense failures, retained as measured failures.
- Frozen 30-question/four-mode migration evaluation: 120 executed; 105 PASS, 15 retained dense FAIL, zero NOT_RUN; see below.

The first full development run retained one UTF-8 BOM/AST-check failure; this
encoding problem was corrected before the successful clean-environment run.
The first developer environment exposed inherited, unrelated global LangChain
dependency conflicts; a clean isolated installation and pip check resolved
the project verification environment without modifying the global packages.

Browser, exact-recall and evaluation requests overlapped part of the measurement.
Migration latency observations are not an isolated performance benchmark.
Dollar costs remain unknown/null; token usage is not a bill.

## Completed migration evaluation

Run: frozen30-20261018T150156741708Z. All 120 cells executed: 105 PASS,
15 FAIL, zero NOT_RUN. All 15 failures are the retained dense cases.
The PASS counts, failed question IDs, top-fix/part counts and refusal counts match
the original frozen run; this is a compatibility observation, not a model-ranking
claim. Each mode passed all three refusal cases.

| Mode | PASS / attempted | FAIL | Top fix | p95 ms | English p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| dense | 15/30 | 15 | 12/27 | 5571.6 | 4182.5 |
| hybrid | 30/30 | 0 | 27/27 | 6595.3 | 6730.0 |
| graph | 30/30 | 0 | 27/27 | 4625.6 | 4625.9 |
| combined | 30/30 | 0 | 27/27 | 7156.0 | 7113.2 |

All English p95 observations still exceed the three-second target. These runs
overlapped some smoke/recall checks; no isolated latency improvement is claimed.
Dollar totals stay null. Raw attempts, usage and synthetic traces were retained.
The SQL baseline check passed: 52 live work orders, original 50 baseline rows
unchanged, two later observations excluded from evaluation without deleting them.
The dashboard's packaged published results remain the original historical run.

## Limits and next steps

The role selector is demo presentation, not production authentication.
Supabase's SQL transaction/outbox and same-event-ID retry remain responsible for
feedback durability; no SQLite fallback was added to the UI. The graph fallback
remains explicitly labeled in-memory NetworkX.

The personal walkthrough/user-manual PDFs are paused and remain locally excluded.
This milestone does not edit or publish them. After verifying the migration,
compare generation/embedding alternatives using a separate controlled benchmark,
preserving model IDs, raw failures, denominators, latency and unknown costs.