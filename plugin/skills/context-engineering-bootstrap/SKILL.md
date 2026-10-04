---
name: context-engineering-bootstrap
description: Generate a baseline ContextSet (Templates, Facets, Value Searches) from a target database's schema (and optional design docs / application code) and save to a caller-specified path. Optionally upload to the Context Store.
---

> **Load [`context-engineering-workflow`](../context-engineering-workflow/SKILL.md) first** for shared terminology, lifecycle overview, and safety protocol.

# Skill: Baseline ContextSet Bootstrapping

## Goal
From a target database and optional user-supplied enrichment sources (design docs, ORM models, sample SQL, glossary), produce a baseline `ContextSet` JSON at a caller-specified path. Optionally upload to the Context Store and return the resource name.

## Prerequisites
- A working DB connection — Toolbox MCP tools (`<source>-list-schemas`) must be visible to the agent throughout the run. If missing or unreachable at any point, stop and route through `context-engineering-init`; do not work around it (no bash `uvx toolbox-server invoke` fallback).
- Target output path for the ContextSet JSON. If not supplied, prompt the user; default `./bootstrap_context.json` at cwd.
- (Optional) Design docs, application code, sample SQL, glossary, or other enrichment sources.
- (Optional, for upload) The target Context Store resource: `project_id`, `location`, and a `context_set_id`. Together they form `projects/<project_id>/locations/<location>/contextSets/<context_set_id>` — see the Context Store (OneMCP) Protocol in `context-engineering-workflow`.

## Guidance

1. **Confirm scope with the user:**
   - Which Toolbox `<source>` to introspect (auto-select if exactly one supported source exists in `tools.yaml`; otherwise prompt).
   - Which schemas / tables to focus on (or all, if the DB is small).
   - Output path for the ContextSet JSON.
   - Whether to upload to Context Store after generation. If yes, collect `project_id`, `location`, and `context_set_id`; then call `list_context_set_locations(project_id)` and confirm the chosen location is in the returned list (if not, let the user pick one from it). Tell the user the exact resource name that will be written.

2. **Collect enrichment sources:** prompt for design docs, ORM models, sample SQL, glossary, etc. Wait for the user's response before proceeding.

3. **Deduce Key Info (Core Execution):**
   - **Targeted Schema & Graph Retrieval**: Informed by the ingested application artifacts and design docs, use the available Toolbox MCP tools configured in the active `autoctx/tools.yaml` (e.g., `<source>-list-schemas`, `<source>-list-graphs`) to fetch the schemas for the target database and relevant tables/graphs.
   - Present the retrieved schema summary **structurally and cleanly** to the user. Ask the user if they want to filter or focus on specific schemas, tables, or graphs.
   - Perform a **deep analysis** of the retrieved **schema and any provided documentation or code** to identify important concepts, relationships, and likely query patterns.
   - **GQL Preference for Graph Entities**: When querying entities or relationships that are modeled within a property graph, **always prefer GQL (`GRAPH <graph_name> MATCH ...`)** over writing relational SQL `JOIN` queries against the underlying node/edge tables.
   - **Collect Candidates**: Identify representative natural language queries with their corresponding SQL/GQL, common filter conditions or business rules (and graph pattern facets), and **columns that require specialized value searching** (e.g., names needing fuzzy match, descriptions needing semantic search).
   - *Review Check:* Briefly display these candidates to the user for approval or modifications before proceeding.  

4. **Identify candidate items:** analyze schema + enrichment to identify representative NLQ + SQL pairs (Templates), common filter fragments (Facets), and columns needing fuzzy/semantic matching (Value Searches). Present the candidates to the user for review before generating.

5. **Generate the ContextSet:** invoke the `context-engineering-generation-guide` skill with the approved candidates. Save items incrementally to the output path via the `mutate_context_set` MCP tool. For a new file, construct `"operation": "add"` mutations for each item.

6. **Validate**: Call `validate_context_set` on `bootstrap_context.json`. If invalid, fix each issue via `mutate_context_set` and re-validate until clean. Stop after two failed attempts and surface remaining issues to the user.

8. **Optionally upload:** if the user opted to upload:
   - Build `context_set = projects/<project_id>/locations/<location>/contextSets/<context_set_id>` and show it to the user. Remind them that if a context set with this name already exists, the upload **overwrites it** and the previous contents cannot be recovered; proceed only on explicit confirmation.
   - Call `upload_context_set(context_set=<context_set>, local_file_path=<output path>, description=<short description>)`.
   - Poll `get_operation` on the returned operation (1 s doubling backoff, 5-minute ceiling) until `done: true` before continuing — see the Context Store (OneMCP) Protocol. On `done: true` the upload is complete; on an `error` field, surface it verbatim and stop.

9. **Summarize:** report the local file path and (if uploaded) the resource name and the operation name that completed.

## Rules
- Caller supplies (or explicitly confirms a default for) the output path.
- Never upload without explicit user consent.
- Always use the `mutate_context_set` MCP tool for ContextSet file changes — pass mutation payloads directly. Do not read the target file beforehand.
- Do not invoke `context-engineering-evaluate` or `context-engineering-hillclimb`.

## Tools

**MCP:**
- `<source>-list-schemas` (Toolbox) — schema introspection.
- `mutate_context_set` — incremental writes to the output JSON.
- `list_context_set_locations` — confirm the upload location before uploading.
- `upload_context_set` — optional Context Store upload; returns an operation.
- `get_operation` — poll the upload operation until `done: true`.

**Sibling skill:**
- `context-engineering-generation-guide` — produces well-formed Template / Facet / Value Search JSON. Also the reference for context-item schema and authoring standards.
