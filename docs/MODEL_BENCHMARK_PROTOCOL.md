# Model comparison protocol

Protocol frozen before inference on 18 October 2026. This is a synthetic application benchmark, not evidence of pharmaceutical or equipment safety.

## Decisions and scope

Keep deployed defaults unchanged: DeepSeek v4.1 Flash generation and Qwen3 Embedding 8B at 1024 dimensions. Compare generation against `accounts/fireworks/models/gpt-oss-120b` and `accounts/fireworks/models/glm-5p3-flash`; both advertised serverless by the authenticated public Fireworks catalog. Availability is not an accuracy result. Retain provider failures without replacing candidates after scoring.

Use the existing immutable 30 questions and verified two-PDF corpus. Construct approved facts from public question text, a local NetworkX projection of the frozen synthetic data, and a read-only SQLite fixture audit. SQLite here is an evaluation fixture, never an application persistence fallback. No expected answers or benchmark labels enter the prompt or context construction. There are 27 supported contexts and three deterministic router/evidence refusal controls. The two router refusals and one unknown-alarm evidence refusal make no model calls and do not establish model refusal ability.

Each generation candidate gets two serial, interleaved passes over the identical contexts, temperature 0, max output 3000 tokens, strict existing JSON schema and exact approved-fact equality. Reverse/rotate model ordering by question and repeat. No retries. Save sanitized raw responses and status codes before aggregate scoring. Report fidelity out of 54 attempted supported calls per model, and latency with its actual denominator. Failed or unavailable calls remain visible. This tests constrained formatting and grounding, not open-ended medical reasoning or creativity.

## Embedding comparison

Preflight existing documented profiles: Qwen3 Embedding 8B (1024), Nomic Embed v1.5 (768), BGE Small English v1.5 (384). Missing provider access yields NOT_RUN for downstream retrieval; the failed preflight remains recorded. For available profiles embed the same 80 canonical documents through the LangChain validated embedding bridge, then run the same 27 supported public questions twice. Use equipment-scoped exact cosine search at k=5 with canonical-ID tie breaking and record ranked IDs/scores before scoring expected source recall. Model-specific dimensions and documented prefixes are retained. Answer keys are only read by the scorer.

These are isolated local vector collections with live provider embeddings. They do not write Pinecone, Supabase or Neo4j. This refines the initial namespace plan: exact local search avoids dimension incompatibility and approximate-index differences. It is not a hosted Pinecone or hybrid latency benchmark. The existing frozen 120-cell dense/hybrid/graph/combined evaluation remains the end-to-end evidence. No new account, paid deployment or hosted database reset is required.

## Reporting and selection

PASS, FAIL and NOT_RUN are separate. Raw attempts precede aggregate files. Record input hashes, model identities, settings, interpreter, usage and HTTP statuses; do not record headers or credentials. Dollar costs remain null without billing evidence. Returned tokens are not a bill. Aggregate median/p95 timings include only their stated populations; record failures separately. No concurrent workloads are intentionally launched during timing.

Recommend a model change only if grounding reliability is maintained across both passes and a useful measured improvement justifies a separate compatibility and deployed end-to-end check. This small synthetic set cannot prove universal superiority. Do not automatically change the application. If embedding alternatives cannot run, recommend retaining the validated current embedding rather than claiming it won a comparison.
## Hosted Nomic follow-up (authorized continuation, 18 October 2026)

The local comparison is complete. The next stage tests Nomic v1.5 at 768 dimensions with actual hosted Pinecone dense/full-text retrieval, live Neo4j traversal and a read-only Supabase baseline check. Keep DeepSeek generation unchanged to isolate the embedding change. Use dedicated index `linemedic-nomic-benchmark-v1` and its model-fingerprinted PDF namespace. Never mix vectors into the current 1024-dimensional app index. Create only this synthetic evaluation index in the existing account, after a successful live embedding probe; provider access/quota failure must be explicit. Unknown dollar costs remain null.

Parse and validate the same two evidence PDFs; ingest 80 canonical baseline sources through LangChain. Verify all payloads and dense/lexical visibility before evaluation. Run all 30 frozen questions in dense, hybrid, graph and combined modes (120 cells), plus the existing four exact-token recall cases for dense/hybrid. No tuning, retries of failed scored cases, SQL/graph reset or feedback submission. Retain raw attempts before aggregates and keep natural routing separate from retrieval ablations. The 50-work-order fixture and read-only frozen graph projection keep denominators comparable; live additional feedback stays untouched.

Compare correctness to the preserved post-migration Qwen run `frozen30-20261018T150156741708Z`, stating that it is a historical reference rather than a simultaneous control for latency. Record the candidate's real hosted provider configuration and ingestion proof. This is a local evaluation runner against hosted services; it is not a candidate deployed to Railway or a deployed-URL candidate test. App defaults and personal PDF files remain unchanged. A model decision follows actual results.