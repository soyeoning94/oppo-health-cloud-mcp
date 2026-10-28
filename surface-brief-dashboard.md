# Dashboard surface brief

Mode: Operate
Primary target: `dashboard.html`

Audience/job: the account owner checks whether data is current, reads the latest personal summaries, understands raw/vendor/local provenance, starts a bounded sync, and copies MCP connection details without exposing credentials in the page.

Constraints: health payloads require bearer authentication; the public shell contains no readings; missing data is a first-class state; no diagnostic claims; desktop and mobile; standard web controls.

## Direction contract

THESIS: A private health-data relay should feel like a dependable city service map: every source, cache, and agent connection has a visible place and condition. It refuses the generic hero-number plus equal metric-card grid.

OWN-WORLD: Restrained transit-atlas palette: midnight service rail, pale mineral content ground, cyan route line, lime healthy service state, amber degraded state, and red only for failures. Compact humanist UI type, tabular numerals, crisp one-pixel rules, rectangular controls with modest rounding. Standard dashboard navigation and controls remain familiar.

STORY: First confirm the relay is alive and when records were last fetched; then scan the latest locally derived summaries and vendor indices; then inspect trend continuity and coverage gaps; finally run a bounded metric sync or copy MCP details. Every number carries provenance.

FIRST VIEWPORT: A dark left service rail holds identity and navigation. The main header shows service state and last refresh. Immediately below, a horizontal source→sync→SQLite→MCP route line exposes the system path. The reading area is asymmetric: a wide latest-snapshot table leads, a narrow vendor-index panel supports it, and the provenance legend is visible without scrolling. Sync controls sit in the header, not in a modal.

FORM: Transit service atlas, candidate 3 of the grounded direction list. Signature move: the live data route line changes station state from current, stale, or unavailable while keeping standard controls. seed:ee831bd7438bce44.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
