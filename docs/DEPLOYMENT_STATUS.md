# Deployment status

## Later hosted verification and local continuation

The later retained smoke report at
`evals/results/railway-deployed-smoke-20261018/REPORT.md` supersedes the source
preparation status below. It records the actual Railway URL
<https://lineagnet-ai-production.up.railway.app> running commit
`0c22e563978b935631ad8b1f825a88a1ba82026d`, with all five hosted checks passing:
seeded alarm, unsupported alarm, exact part lookup, feedback persistence and
supervisor evaluation view. Neo4j was live. Feedback was verified through a fresh
SQL client and application disconnect/reconnect, not a container restart.
These are retained observations from the earlier run, not new deployed checks.
Actual dollar costs remain unknown.

The subsequent Graph Atlas changes are verified locally and remain uncommitted
and undeployed. Hosted atlas checks are **NOT_RUN**. See
[Graph Atlas verification](GRAPH_ATLAS_VERIFICATION.md) for the current checks.

## Historical source preparation record

Status on 18 October 2026: **Railway source preparation complete; hosted deployment unverified.**

The repository includes a multi-stage Docker build, Railway configuration, an exact
deployed-host allowlist, a fail-closed shared demo password gate and a packaged
historical evaluation summary. See [Railway setup](RAILWAY_SETUP.md).

## Verified locally

- Python: 317 passed, 20 skipped, 5 upstream PyMuPDF warnings.
- React state tests: 6 passed. TypeScript and frontend build passed in Docker.
- Actual Linux Docker image ran as a non-root user.
- HTTP health: 200; unauthenticated catalog: 401; authenticated dashboard/catalog: 200.
- Packaged catalog: 20 alarms, 10 equipment, 30 sections, 2 PDFs, 50 baseline work orders.
- Packaged historical evaluation: 15/30 dense, 30/30 hybrid, 30/30 graph, 30/30 combined.
- Configured secret scan: no matching values in Git-eligible files or frontend bundle.

Local test logs and the container smoke response remain in ignored `evals/results/`.
Original supplied planning PDFs, credentials, raw traces and local result logs are
excluded from the source repository. Published evaluation files contain reviewed
synthetic aggregates and provenance only.

## Hosted smoke checks

No actual deployed URL or deployed commit has been verified. All five requested
hosted checks remain **NOT_RUN**: seeded alarm, unsupported alarm, exact part
lookup, feedback persistence and supervisor evaluation view. Local tests and
source publication do not count as hosted checks.

The remaining dependency is a Railway service connected to the source repository,
configured provider variables, a generated domain and shared demo credentials.
No hosted feedback observation has been written during source preparation.

## Spending and limitations

Actual deployment spend is unknown. Railway Hobby has a $5 monthly minimum with
$5 of included resource usage; resource usage above that and external provider
charges are additional. See [current pricing](https://docs.railway.com/pricing/plans).
No deployment resource or inference request was provisioned by the local Docker checks.

The application remains synthetic-only, with demo roles and a shared access gate,
not production identity or GxP validation. Dense retrieval has known failures;
measured English p95 latency exceeds the target. Multi-instance connection state
and deployed restart persistence have not been verified.
