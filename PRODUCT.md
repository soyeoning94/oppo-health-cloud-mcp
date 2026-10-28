# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: Python 3.12 service using the existing `mcp[cli]` 1.x FastMCP stack, Streamable HTTP MCP, SQLite cache, and a dependency-light server-rendered/static management dashboard. Deployment target is the user's Tencent Cloud Ubuntu server.

## Users

Primary user: the owner of the OPPO Health account, checking personal watch/phone health data and connecting multiple AI agents to the same private data service.

Secondary users: AI agents explicitly given the MCP bearer token by the owner.

## Product Purpose

Provide a durable, remotely reachable read/sync layer for the owner's OPPO/HeyTap health data. Success means the owner can see synchronization state, data coverage, useful summaries, raw-versus-derived provenance, and can connect different MCP-compatible agents without copying the local database between machines.

## Positioning

The service reproduces the OPPO Health cloud request signature and exact curl/OkHttp-like request shape, then turns sparse vendor records into a private SQLite-backed MCP interface and an honest data-quality-aware dashboard.

## Operating Context

- Runs continuously on Tencent Cloud at `115.159.96.81`.
- Pulls data from `sport.health.heytapmobi.com` using the owner's `auth.json`.
- Health data and access credentials are sensitive and must not appear in logs, HTML source, screenshots, source control, or public API responses.
- Multiple agents connect over MCP Streamable HTTP using an explicit bearer token.
- The management dashboard is used in a browser and must remain useful on desktop and mobile.

## Capabilities and Constraints

- 67 metric names: 65 independent metrics plus `stress` and `physical_mental` composite datasets.
- Four MCP tools remain authoritative: `cloud_sync`, `cloud_query`, `cloud_analyze`, `cloud_status`.
- Dashboard statistics must label their provenance as vendor-provided, locally derived, or raw records.
- The data is sparse; a successful empty response is not proof of no health event.
- `sleep_stat` is unreliable for this account; sleep duration should be locally aggregated from `sleep_status` when possible.
- `sport_file` cannot return decrypted tracks without an app-generated `encryptKeyEnc`.
- OPPO API calls must continue through the curl subprocess with the exact header set; Python HTTP clients are rejected by TLS fingerprinting.
- `mcp[cli]` must stay below 2.x because the source imports `mcp.server.fastmcp`.
- This is an informational personal-data service, not a diagnostic medical device. Derived flags describe data patterns and coverage, not diagnoses.
- Assumption delegated by the deployment brief: a single-owner private service is sufficient; there is no multi-user account model.

## Evidence on Hand

- Existing upstream source at commit `c9b16e87b0bb51ad15a3196becdb779b46d21ec2`.
- Deployment handoff document at `C:\Users\32636\Downloads\Telegram Desktop\HANDOFF-移交文档.md` with verified API behavior, units, data semantics, known gaps, and four required bug fixes.
- A valid private `auth.json` extracted locally from that handoff; it is gitignored and must be deployed separately with mode 600.
- No approved brand assets, clinical reference ranges, medical claims, or public testimonials exist; future work must not fabricate them.

## Product Principles

1. Provenance before interpretation: every summary says whether it is vendor data, local calculation, or raw data.
2. Missingness is visible: coverage and stale-data warnings are first-class information.
3. Private by default: no health payload without bearer authentication and no secrets in logs.
4. Useful without pretending to diagnose: show trends, comparisons, units, and data quality; avoid medical conclusions.
5. One source of truth: dashboard and every connected agent read the same SQLite cache and MCP implementation.

## Accessibility & Inclusion

Use semantic HTML, keyboard-operable controls, visible focus, sufficient contrast, responsive layouts, and non-color-only state labels. Tables need mobile-safe alternatives or horizontal containment.
