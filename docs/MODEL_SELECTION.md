# Accuracy-first model selection

On 18 October 2026 the user selected **DeepSeek v4.1 Flash generation + Nomic Embed v1.5 embeddings**, using **Hybrid + graph** as the React/dashboard and omitted-mode HTTP default. LangChain handles documents, section splitting, ingestion, retrieval and structured generation. LangGraph is not used.

## Configuration and evidence

- Generation stays `accounts/fireworks/models/deepseek-v4p1-flash`: all three generation candidates preserved approved facts on 54/54 calls each, so there was no measured accuracy reason to switch it.
- Embeddings use `nomic-ai/nomic-embed-text-v1.5`, 768 dimensions, with the existing documented search_query/search_document prefixes.
- Selected Pinecone index: `linemedic-nomic-benchmark-v1`, cosine, dense field `embedding`, full-text field `body`. The reviewed 80-source PDF namespace is `linemedic-pdf-e9f7719f8e9ed29e-lc-v1-f67f56b15377cb8e-1ab9ab714252`.
- The original Qwen 1024-dimensional index and its namespaces remain intact. No vectors are mixed between dimensions/models.
- The frozen hosted run `frozen30-20261018T154738753431Z` measured 114 PASS / 6 FAIL / 0 NOT_RUN: dense 24/30, hybrid 30/30, graph 30/30, combined 30/30. The historical Qwen run had 105 PASS / 15 FAIL.
- Nine diagnosis cases improved; none regressed in that 120-cell comparison. All five Tamil identifier/grounding cases passed in every mode; language quality was not scored.
- Six dense failures remain. Dense full-recall gates still failed all four exact-token checks, including two lower source-recall fractions than Qwen. Hybrid passed all four. This supports the hybrid/combined choice rather than dense-only use.

## Runtime and publication

Explicit Smart router, dense, hybrid and graph options remain available. Safety/context routing and exact fact, part, source and rate checks remain authoritative. The Streamlit entry point is preserved with its existing routing behavior. Provider keys remain server-side. Supabase feedback and graph outbox semantics are unchanged.

The selected Nomic aggregates are published in a new directory alongside the unchanged original Qwen results. The published latest pointer selects Nomic; historical raw outputs remain local. Evaluation lab displays saved measurements rather than running an evaluation on page load.

Personal manual, walkthrough and application PDFs stay under `output/pdf/guides/`, excluded from both Git and Docker build context. Only the two synthetic evidence PDFs are application data.

The release must verify the deployed source revision, combined default, DeepSeek model and Nomic namespace, known alarm/part/refusal, SQL-audited graph, physical PDF page, current supervisor history and historical evaluation. No new feedback observation is needed for this rollout. Raw acceptance receipts and actual cost (unknown unless billed) are recorded in ignored `evals/results/`.

See [benchmark protocol](MODEL_BENCHMARK_PROTOCOL.md) and [measured results](MODEL_BENCHMARK_RESULTS.md). This remains a synthetic demonstration, not validated GxP software or production authentication.