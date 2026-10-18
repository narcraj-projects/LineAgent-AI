# Model benchmark results

Run `model-benchmark-20261018T153222Z`, 18 October 2026. Base deployed source `28fd247ca2495a740eef4e2176c8f9d52a017af7`; benchmark additions are separate evaluation code. Defaults and hosted data were unchanged.

Accuracy-first recommendation: retain DeepSeek v4.1 Flash for generation; the generation candidates tied on exact fact preservation, so there is no measured accuracy reason to replace it. Nomic v1.5 is the stronger embedding candidate on the frozen hosted diagnosis suite: 114/120 correct versus Qwen 105/120, with nine improvements and no diagnosis-case regressions. Use hybrid or hybrid+graph retrieval: both embeddings scored 30/30 there, whereas Nomic dense still failed six cases and all four unscoped full-recall gates. Two dense exact-token recall fractions were worse than Qwen. Do not claim universal superiority. The user subsequently selected Nomic with hybrid/combined retrieval on 18 October 2026. The rollout uses the separate 768-dimensional index and requires deployed acceptance checks. See MODEL_SELECTION.md. Personal PDFs are being updated locally after that decision.

## Generation

| Model | Exact facts | Median ms | p95 ms | Max ms |
|---|---:|---:|---:|---:|
| deepseek-v4p1-flash | 54/54 | 2261.6 | 3516.3 | 14970.3 |
| gpt-oss-120b | 54/54 | 2129.6 | 2920.4 | 3737.9 |
| glm-5p3-flash | 54/54 | 2710.2 | 4308.6 | 4417.5 |

Each denominator is 54 supported calls: 27 unique public questions, two repeats, temperature 0, max output 3000, no retry. All 162 calls passed strict response validation and exact approved-fact equality. GPT-OSS median was 5.8% lower and p95 16.9% lower than DeepSeek in this sample. No statistical superiority claim; not deployed end-to-end timing. DeepSeek had one 14.97-second outlier, retained in raw data. GLM was slower.

Three control questions (two router refusals and one unknown-alarm evidence refusal) made no model calls. They do not establish model safety. The task is constrained fact preservation, not open-ended diagnosis.

## Embeddings

| Model | Dimension | Completed | Mean source recall@5 | All expected sources | Median ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| fireworks/qwen3-embedding-8b | 1024 | 54/54 | 0.6296 | 12/54 | 330.7 | 818.1 |
| nomic-ai/nomic-embed-text-v1.5 | 768 | 54/54 | 0.7500 | 26/54 | 428.9 | 817.9 |
| BAAI/bge-small-en-v1.5 | 384 | 54/54 | 0.6481 | 8/54 | 394.1 | 515.2 |

All three real embedding preflights passed; no downstream cells were NOT_RUN. Each model embedded the same 80 canonical sources through the validated LangChain bridge. Each made 54 query/search observations: 27 unique questions twice. PASS here means provider/search completion, not perfect recall. Nomic improved mean expected-source recall by 12.0 percentage points over Qwen, with a higher median query/search latency. BGE had slightly higher mean recall than Qwen but fewer fully recalled cases.

Search used separate local collections with model-specific dimensions, equipment filtering, exact cosine, k=5 and canonical-ID tie breaking. It did not write Pinecone or benchmark approximate/hybrid search. Existing documented model prefixes were retained. Local vector collections replaced the initial namespace plan to avoid dimension/index confounds. At the local-comparison stage, hosted hybrid/combined performance was NOT_RUN. The completed hosted follow-up is recorded below.

## Preservation, audit and reproducibility

- Protocol was saved before inference: [model benchmark protocol](MODEL_BENCHMARK_PROTOCOL.md). Input question hash and corpus digest are in the run manifest.
- Rerun with `.\.venv-langchain\Scripts\python.exe -m evals.model_benchmark`. Public generation candidates are frozen; a current catalog check stops before inference if they are no longer advertised serverless. Models are not silently replaced after scoring.
- Raw JSONL attempts precede aggregation. Local run directory contains contexts, router controls, generation responses, embedding preflights, document vectors, query rankings, HTTP statuses, usage, summaries and manifest. Error bodies/headers are not recorded.
- Initial offline context setup stopped because its assertion incorrectly expected the unknown-alarm control to have a router-refuse route. The evidence guard correctly refused it. The assertion was fixed before any provider call; no model attempts were discarded. The empty initial directory and a setup-attempt receipt are retained.
- The public provider catalog was captured before candidate selection. All three embedding profiles also passed actual inference even though the newer management catalog advertised only Qwen 8B as serverless embedding; catalog omission alone was not treated as proof of unavailability.
- No original synthetic records, evidence PDFs, published results, prior raw results, live SQL/graph feedback or Pinecone namespaces were changed.
- Actual cost in USD is null. Reported token usage is not billing. No tariffs or model-price advantage are claimed.

## Existing end-to-end reference

The post-LangChain frozen run `frozen30-20261018T150156741708Z` matched original correctness counts and failed IDs: dense 15/30, hybrid/graph/combined 30/30 each; dense supported top-fix 12/27, others 27/27; three refusals passed per mode. Dense exact-token full-recall gates remained 0/4, hybrid 4/4. English p95 targets were not met. Keep those failures visible. Generation comparison does not fix retrieval errors.

## Limits and recommendation

This small synthetic set, with repeated identical contexts, cannot establish general superiority, production safety, general Tamil fluency or operational cost. Embedding model order was fixed, so latency may include time-of-run effects; generation order rotated serially. No concurrent test/browser workloads were intentionally run during measured provider timing. The three PDF guides and raw attempts stay local; reviewed protocol, runner, tests and this synthetic aggregate report are repository documentation.

The initial local-only recommendation was to retain defaults pending the hosted follow-up. That follow-up is now complete below. No LangGraph was added; the app still uses its original deployed defaults pending the user decision.

## Milestone verification

Python 3.13.5 in the clean `.venv-langchain` environment: `python -m pytest -q --basetemp=tmp/pytest-model-benchmark` completed with **350 passed, 20 skipped, 7 warnings** in 45.79 seconds. Offline network-disabled imports include the new catalog adapter and benchmark runner. Five warnings are upstream PyMuPDF deprecations; two are pytest-cache directory permissions. Skips remain NOT_RUN. Fresh authenticated hosted browser walkthrough capture passed; no feedback was submitted. Frontend state/build were unchanged, so their previously verified 10 tests/build were not rerun.

## Hosted Nomic follow-up completed

Run `frozen30-20261018T154738753431Z`; receipt directory `nomic-hosted-20261018T154706Z`. This used the actual hosted Pinecone document/full-text API and live Neo4j, with DeepSeek generation held fixed. Supabase baseline verification passed: 52 live work orders, the unchanged original 50 and two additional feedback records. The evaluator used the frozen read-only SQLite fixture and a live graph projection restricted to original work orders; it did not claim those fixture reads were live Supabase diagnosis reads. The separate live-history application smoke below used actual Supabase.

| Retrieval mode | Qwen reference | Nomic candidate | Nomic supported top fix / exact parts |
|---|---:|---:|---:|
| dense | 15/30 | 24/30 | 21/27 |
| hybrid | 30/30 | 30/30 | 27/27 |
| graph | 30/30 | 30/30 | 27/27 |
| combined | 30/30 | 30/30 | 27/27 |

**114 PASS, 6 FAIL, 0 NOT_RUN**. Compared with 105 PASS/15 FAIL for the preserved Qwen reference, nine diagnosis cells improved and none regressed. The improved dense questions were `SYN-Q-004`, `SYN-Q-006`, `SYN-Q-009`, `SYN-Q-013`, `SYN-Q-014`, `SYN-Q-015`, `SYN-Q-018`, `SYN-Q-024`, `SYN-Q-029`. All other paired diagnosis outcomes were unchanged.

All three refusal cases passed in every mode. Hybrid/graph/combined had 27/27 exact fixes, parts, valid source coverage and audited rates. Tamil identifier/grounding checks were 5/5 in every mode; Qwen dense was 4/5. Tamil output-language quality remains unscored. Six Nomic dense failures remain: `SYN-Q-011`, `012`, `016`, `017`, `019`, `020` (all in the SYN-Q namespace). They refused because positive evidence was insufficient; these expected-supported cases still count as FAIL, never successful refusals.

### Exact-token limits remain

| Token | Qwen dense recall@5 | Nomic dense recall@5 | Nomic hybrid recall@5 |
|---|---:|---:|---:|
| SYN-ALM-001 | 0.40 | 0.40 | 1.00 |
| SYN-ALM-020 | 0.40 | 0.20 | 1.00 |
| SYN-PART-006 | 0.50 | 0.25 | 1.00 |
| SYN-PART-007 | 0.00 | 0.50 | 1.00 |

Both models failed all four dense full-recall gates. Nomic improved one fraction and regressed two; those source-recall regressions must not be hidden by the nine diagnosis gains. Hybrid passed all four gates for both models. For accuracy, retain hybrid/combined as the operational strategy rather than relying on dense-only search.

### Live services, preservation and verification

- Provisioned only `linemedic-nomic-benchmark-v1`, 768-dimensional cosine, with an English non-stemming full-text body field and deletion protection. The LangChain/PDF namespace is `linemedic-pdf-e9f7719f8e9ed29e-lc-v1-f67f56b15377cb8e-1ab9ab714252`. All 80 documents were acknowledged and visible through dense/full-text search. This isolated benchmark resource is retained; actual dollar costs are unknown.
- A read-only Application-service smoke used actual live Supabase, Neo4j, Pinecone and Fireworks for a known alarm, exact part and unknown alarm. All three checks passed. Candidate rates matched fresh SQL history, including existing feedback. No Application.start/seed, feedback submission or graph-sync write was performed. SQL snapshots remained identical, work orders stayed 52, and the outbox stayed unchanged with zero pending events.
- The original Qwen 1024-dimensional index and all 80 source payloads were read-verified intact. Hashes of 24 frozen artifacts and personal PDF drafts were unchanged. Baseline latest pointers were restored. Prior published/raw results were retained.
- Full Python regression: Python 3.13.5, **351 passed / 20 skipped / 5 upstream PyMuPDF warnings**, 45.98 seconds. Fresh offline imports include the hosted benchmark runner. Frontend code was unchanged; its prior state/build/browser checks were not relabeled as new candidate deployment checks.
- The frozen run retained all 120 raw rows before aggregates and 120 acknowledged synthetic trace uploads. Exact-token gate failures are retained separately. Nonzero evaluation exit codes reflect scored failures, not a hidden aborted run.
- The candidate was evaluated from local runners against hosted services and through the actual application service layer. It was **not deployed to Railway**. No model configuration, environment file, app source, feedback ledger or PDF was changed. No source or PDF push was made.

Reproduce with `.\.venv-langchain\Scripts\python.exe -m evals.hosted_embedding_benchmark` using existing server-side credentials. This command explicitly targets the isolated candidate index, verifies the embedding profile before creation, ingests the verified corpus and runs the frozen and exact-token suites. Never repoint production merely to run the benchmark. The original pre-inference local protocol is also retained as `protocol-at-run.md` with the exact manifest hash; the protocol file now has a dated hosted continuation.

### Accuracy-first decision

Accuracy-first recommendation: retain DeepSeek v4.1 Flash for generation; the generation candidates tied on exact fact preservation, so there is no measured accuracy reason to replace it. Nomic v1.5 is the stronger embedding candidate on the frozen hosted diagnosis suite: 114/120 correct versus Qwen 105/120, with nine improvements and no diagnosis-case regressions. Use hybrid or hybrid+graph retrieval: both embeddings scored 30/30 there, whereas Nomic dense still failed six cases and all four unscoped full-recall gates. Two dense exact-token recall fractions were worse than Qwen. Do not claim universal superiority. The user subsequently selected Nomic with hybrid/combined retrieval on 18 October 2026. The rollout uses the separate 768-dimensional index and requires deployed acceptance checks. See MODEL_SELECTION.md. Personal PDFs are being updated locally after that decision.

The Qwen reference is historical, not a simultaneous timing control. No speed or cost superiority is inferred from this follow-up. This one synthetic run cannot establish general production accuracy or safety.
