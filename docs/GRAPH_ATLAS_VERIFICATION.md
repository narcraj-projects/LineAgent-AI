# Graph Atlas verification

Observed 18 October 2026. The interrupted Graph Atlas milestone is complete locally.
The provider status now has its own accessible label so browser checks can verify
the provider independently of the explanatory caption. Mobile screenshot capture
waits for navigation to finish sliding offscreen.

## Actual checks

| Check | Result |
| --- | --- |
| Python 3.13.5, `python -m pytest -q --basetemp=tmp/pytest-atlas-resume` | 324 passed, 20 skipped |
| Fresh-process offline import smoke checks | PASS, included in pytest for all source modules |
| React state tests, `npm.cmd --prefix web run test` | 10 passed across 2 files |
| TypeScript and Vite, `npm.cmd --prefix web run build` | PASS |
| Headless Edge suite against `http://127.0.0.1:18766` | 4 passed |
| Atlas-only browser recheck after mobile capture adjustment | 1 passed |
| `git diff --check` | PASS |

Pytest emitted five upstream PyMuPDF deprecation warnings and two cache-write
permission warnings. The 20 skipped integration cases are NOT_RUN, not live passes.
React state tests use explicit doubles. The separate live browser cases used
configured services; the atlas displayed `neo4j / live` and SQL-audited histories.
No feedback was submitted by these browser tests.

The browser checked canonical graph IDs, recorded outcome denominators, exact PDF
page links, filters, search, layouts, zoom/reset, alarm changes, mobile overflow,
and the handoff to combined investigation. Desktop and mobile screenshots were
visually inspected. The mobile graph intentionally extends inside its clipped
canvas and supports panning; the page itself has no horizontal overflow.

## Evidence and boundaries

Logs remain in ignored `evals/results/atlas-resume-python.txt`,
`atlas-resume-browser.txt` and `atlas-resume-mobile-check.txt`. Screenshots remain
in ignored `tmp/atlas-desktop.png`, `atlas-fix-inspector.png` and `atlas-mobile.png`.
The earlier failed browser attempt remains in `evals/results/atlas-browser-tests.txt`.
Frozen baseline and evaluation artifacts were not changed or rerun.

Repository HEAD is `0c22e563978b935631ad8b1f825a88a1ba82026d`; the atlas changes
are uncommitted and have not been deployed. Deployed atlas checks are NOT_RUN.
The existing Railway deployment and its earlier smoke results are recorded in
`DEPLOYMENT_STATUS.md`; those results do not verify this working tree.

Actual dollar cost is unknown (`null`); live browser checks may incur provider
usage. Demo roles remain presentation controls, not production authentication.
Graph paths include linked decoys as evidence, not recommendations. Investigation
continues to enforce positive support and source/part/fix/rate validation.
