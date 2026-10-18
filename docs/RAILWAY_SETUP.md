# Railway deployment

The service uses the root Dockerfile to build React and package it with FastAPI,
the two synthetic PDFs, frozen data and a reviewed historical evaluation summary.
It runs one Uvicorn worker in one replica. Supabase remains the durable store.

## Service configuration

1. Select this GitHub repository in a Railway project. Keep the root directory `/`.
2. Use the repository's `railway.json`: Dockerfile builder, `/api/health` health
   check, one replica. Leave custom build and start commands unset.
3. In Settings → Networking, generate a Railway domain. Railway provides
   `RAILWAY_PUBLIC_DOMAIN`; it is an exact hostname allowlist, not a wildcard.
4. In Variables, set provider credentials from your existing private configuration:
   `FIREWORKS_API_KEY`, `FIREWORKS_MODEL`, `FIREWORKS_EMBEDDING_MODEL`,
   `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `NEO4J_URI`, `NEO4J_USERNAME`,
   `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`. Optional LangSmith settings are in `.env.example`.
5. Add `LINEMEDIC_DEMO_USERNAME` and a new `LINEMEDIC_DEMO_PASSWORD` with at
   least 16 characters. Keep them in Railway Variables. They protect the shared
   demo and its API; the application's role switch remains a demonstration.
6. Keep `LINEMEDIC_PDF_MANIFEST=output/pdf/manifest.json` (the Docker image sets it).
   Let Railway supply `PORT`; do not copy a Windows `SSL_CERT_FILE` path.
7. Deploy. The browser will prompt for the shared username/password. Connect the
   services in the UI and check that the displayed provider is live or fallback.

Health returns 503 while the domain or demo credentials are missing. Healthy HTTP
status does not prove provider access. The catalog and historical evaluation view
can work before connection; diagnosis and supervisor history need live services.

After signing in, `/api/configuration` reports required variable presence,
chat-model format, an accidental publishable Supabase key, custom CA-file existence
and the Railway source revision when supplied. It never returns credential values
and does not claim that a configured service has been contacted successfully.

## Verification after publication

Record the actual HTTPS URL and source commit. Execute these against that URL:

| Check | Required observation |
| --- | --- |
| Seeded alarm | Supported canonical fix and traceable source IDs |
| Unsupported alarm | Safe refusal, no invented candidate |
| Exact part lookup | Correct canonical part in scoped retrieved evidence |
| Feedback persistence | One deliberate synthetic event; same-ID retry; verified after reconnect/restart |
| Supervisor evaluation | Four saved modes with 15/30, 30/30, 30/30 and 30/30; unknown costs retained |

Do not substitute local test results for these checks. Feedback testing writes a
synthetic observation; preserve it rather than resetting baseline history. Keep
raw responses in ignored local result files, with credentials and headers excluded.

## Costs and limits

Railway Hobby currently has a $5 monthly subscription including $5 resource usage;
usage above the allowance increases the bill. This is not a total project quote.
See [Railway pricing](https://docs.railway.com/pricing/plans) and configure account
spending controls. Fireworks, Pinecone, Neo4j, Supabase and optional tracing are
separate. Actual spend is unknown until usage/billing is observed.

The shared gate is not per-user authentication, an audit-grade access system or
GxP authorization. This remains synthetic-only and unsuitable for real equipment
maintenance. No successful hosted smoke run is implied by these instructions.
