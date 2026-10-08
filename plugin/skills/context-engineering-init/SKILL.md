---
name: context-engineering-init
description: Ensure the environment is ready for context-engineering work — manage the Toolbox `tools.yaml` for database connections, verify runtime and GCP setup (uv, evalbench, ADC, Dataplex/GDA APIs, IAM), initialise the experiment `state.md` (Context Store coordinates and run preferences), and diagnose readiness failures raised by other skills.
---

# Skill: Environment & Connection Setup

## Goal
Ensure the caller's environment is ready for context-engineering work: Toolbox `tools.yaml` in place with at least one verified DB source, the experiment `state.md` initialised with the Context Store coordinates and run preferences that downstream skills read instead of re-asking, and (when asked or when downstream failures need diagnosis) runtime + GCP readiness verified. Manage `tools.yaml` on request; run any subset of checks on request.

## Prerequisites
- `gcloud` CLI on PATH.
- (Optional) Target GCP project id — if not provided, use ADC's default project.
- (Optional) Existing `tools.yaml` to amend rather than overwrite.

## Guidance

Pick the flow that matches the user's intent:

### Manage `tools.yaml` (DB connections)

Three sub-workflows:

**Create new**
1. Identify the DB type. The supported databases are: Cloud SQL Postgres, Cloud SQL MySQL, AlloyDB Postgres, Spanner GoogleSQL (Graph supported), Spanner PostgreSQL (no Graph support), Cloud Bigtable, and Firestore (MongoDB API). Load the corresponding `references/<db_type>.md` for required fields.
2. Ask the user for every required field explicitly. Do not fill in missing values.
3. Generate the YAML from the template with the user's values.
4. Save to `.context-engineering/tools.yaml`. This path is fixed — the Toolbox MCP server reads it directly; any other path won't be picked up.

**Add to existing**
1. Identify the new DB type and a unique `<source>` name.
2. Ask for the required fields (same as Create).
3. Read the current `tools.yaml`.
4. Generate new `sources:` and `tools:` entries under the unique `<source>` name and append them.
5. Save the updated file.

**List existing**
1. Read the `tools.yaml` at the given path. If missing, tell the user.
2. Parse and list all names under `sources:`.

Validate the target source(s) standalone (Example: `uvx toolbox-server@1.4.0 --config <path> invoke <source>-list-schemas`) — no MCP restart needed for validation. On validation failure, drop into the checks below to diagnose (e.g., `DB source reachable`, `ADC configured`).

After any write, instruct the user to restart the MCP server so downstream skills see the new source:
- Gemini CLI: `/mcp reload`
- Claude Code: `/mcp` → `toolbox` → Reconnect (or `/quit` and relaunch)
- Antigravity CLI: `/mcp` → `toolbox` → Restart

### Initialise the experiment `state.md`

Run this when a user is starting a hill-climb experiment, or when a downstream skill routes here because `.context-engineering/experiments/<experiment_name>/state.md` is missing or incomplete. Everything below is asked **once**; downstream skills (`dataset-generation`, `bootstrap`, `hillclimb`, `evaluate`, `workflow`) read the answers from `state.md` and never re-ask. If the file already exists, only fill in the missing bullets — do not overwrite existing values or any `## Iteration Log`.

1. **Experiment name** — ask for a short slug (e.g. `retail-graph`). The workspace root is `.context-engineering/experiments/<experiment_name>/`.
2. **Project** — the GCP project id (default: ADC's project).
3. **Location** — call `list_context_set_locations(project_id)` and let the user pick from the returned list (if they named one, confirm it is in the list). Do not assume a default.
4. **Context set id** — the name the **final** context set is published under. Default: the experiment name. Derive `Final resource` = `projects/<project>/locations/<location>/contextSets/<context_set_id>` and `Draft resource` = `…/contextSets/<context_set_id>_draft`.
5. **Overwrite acknowledgement** — probe the bare id with `get_context_set(Final resource)`.
   - NOT_FOUND → nothing to overwrite; record `Overwrite acknowledged: yes`.
   - Exists → tell the user the exact resource name, explain that the hill-climb publish will **silently replace** its contents, and ask for an explicit yes. Record `Overwrite acknowledged: yes` only on an explicit yes; otherwise ask for a different `Context set id` and probe again.
6. **Seed resource** (optional) — an existing Context Store resource name to start from. Record it as `Seed resource: <resource name>`; the loop reads it once and never deletes or overwrites it. Otherwise record `Seed resource: (none — seeded from bootstrap)` or `Seed resource: (local file: <path>)`.
7. **Upload approval** — ask once: *"Should I upload each improved draft to the Context Store automatically, or pause for your approval before every upload?"* Record `Auto-approve uploads: true` (automatic) or `false` (pause before each `_draft` upload).
8. **Loop parameters** — offer the defaults and record whatever the user settles on: `Tuning target: 1.0`, `Plateau k: 3`, `Max iterations: 10`.
9. **Enrichment sources** (optional) — design docs, application code paths, or notes that `bootstrap` should read. Record as `Enrichment sources: [...]`.
10. **Active database** — from `tools.yaml`, record the Toolbox `<source>` name and type under `## Active Database`. For Spanner GoogleSQL, call `<source>-list-graphs` and record the property graph ids as `- **Graph Ids**: [...]` (`[]` if none) — `generate_evalbench_configs` reads this bullet.
11. **Existing dataset** — the golden dataset and its splits live at the **DB level**, shared by every experiment on this connection: `.context-engineering/golden.json`, `.context-engineering/splits/hillclimb.json`, `.context-engineering/splits/holdout.json`. If they already exist (a previous experiment generated them), record them now as `Golden dataset`, `Hillclimb dataset`, `Holdout dataset` bullets and tell the user the experiment will reuse them. If they don't, leave the bullets out — `context-engineering-dataset-generation` fills them in.

Write the file in the format shown in `context-engineering-hillclimb/references/workspace.md` (a `## Metadata` section with the bullets above, then `## Active Database`). Confirm the path and the recorded values back to the user in one short summary.

### Verify environment (broad or scoped)

Run any subset of the checks below. Report `PASS` or `FAIL` per check. For any `FAIL`, propose a fix and ask the user for consent before executing anything mutating (installing packages, enabling APIs, changing IAM, writing files).

- **Broad verification** ("am I ready?") → run all checks.
- **Scoped diagnosis** (a downstream skill failed) → run only the checks whose `— required by …` line references the failing operation.

## Checks

Commands in parentheses are examples — the agent may use its own approach.

### Environment
- **`uv` installed** — required to run Toolbox and Evalbench via `uvx`. (Example: `uv --version`; install via `curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`.)
- **Evalbench reachable** — required by `context-engineering-evaluate`; verifying also warms the uvx cache so the first `evaluate` run is fast. (Example: `uvx google-evalbench@1.10.0 --help`.)

### GCP authentication
- **ADC configured** — required by every GCP API call (Context Store, QueryData, Dataplex). (Example: `gcloud auth application-default print-access-token`; fix via `gcloud auth application-default login`.)
- **ADC quota project set** — required by Context Store; the `X-Goog-User-Project` header is derived from it. Missing → 400 on upload/download. (Example: `gcloud auth application-default print-quota-project`; fix via `gcloud auth application-default set-quota-project <project>`.)

### GCP API enablement
- **Dataplex API** (`dataplex.googleapis.com`) — required by every Context Store tool (`list_context_set_locations`, `upload_context_set`, `get_context_set`, `delete_context_set`, `get_operation`). See the Context Store (OneMCP) Protocol in `context-engineering-workflow` for how these are used.
- **Gemini Data Analytics API** (`geminidataanalytics.googleapis.com`) — required by QueryData (used inside `context-engineering-evaluate`).

(Example: check enablement via `gcloud services list --enabled --project=<project>`; enable via `gcloud services enable <api> --project=<project>`.)

### GCP IAM (operational probes)
- **Context Store access** — required by every Context Store tool and by QueryData's context lookup. Probe by calling `get_context_set` on a well-formed resource name that is known not to exist, e.g. `projects/<project>/locations/us-central1/contextSets/preflight-probe-does-not-exist`. Interpret the outcome:
  - **NOT_FOUND** (or INVALID_ARGUMENT) → the request was authenticated and authorized and reached the API; the probe **passes**. This is the expected result.
  - **UNAUTHENTICATED** → ADC is missing or expired; point the user back to the GCP authentication checks above.
  - **PERMISSION_DENIED** → surface the error verbatim and ask the user to request the appropriate Context Store role from their IAM admin.
  - Anything else → surface the error verbatim; do not guess at the cause.
- **GDA access** — required by QueryData (used by `evaluate`). Probe by attempting a lightweight QueryData call in `<project>`. On 403, same handling.

### Toolbox configuration
- **`tools.yaml` present** — required by every skill that reads database schemas (`bootstrap`, `evaluate`, `hillclimb`). Check `.context-engineering/tools.yaml` (the fixed path the Toolbox MCP server reads). Missing → run the Create sub-workflow above.
- **DB source reachable** — required by any Toolbox invocation on that source. For each configured `<source>` in `tools.yaml`, verify Toolbox can list its schemas standalone. On failure, surface the error verbatim; common causes are ADC, wrong project/region, DB IAM, or network. (Example: `uvx toolbox-server@1.4.0 --config <path> invoke <source>-list-schemas`.)

## Rules
- Never execute mutating actions (`gcloud services enable`, `gcloud projects add-iam-policy-binding`, package installs, file writes) without explicit user consent — surface the exact command and let the user run it, or ask consent before running.
- ADC only for DB auth. Never write username/password into `tools.yaml`.
- `tools.yaml` always lives at `.context-engineering/tools.yaml` — do not offer or accept a different path. If the file exists, ask whether to append or overwrite.
- Do not guess DB connection details. Ask the user for every required field explicitly.

## Credentials message (use when collecting DB info for `tools.yaml`)

> "I'll help you configure the database connection in `tools.yaml`. The Toolbox server uses Application Default Credentials (ADC) for authentication, so you don't need to provide a username or password. Please ensure the IAM account you're using has the required permissions to access the database.
>
> Could you please provide the following details:
> - Google Cloud Project ID:
> - Region: (or Instance ID / Database ID for Spanner)
> - Dialect: (for Spanner: GoogleSQL [default] or PostgreSQL)
> - Target tables or property graphs to focus on (optional):
> - ... (other required fields based on database type)"

## References
- `references/<db_type>.md` (`alloydb-postgres.md`, `cloud-sql-mysql.md`, `cloud-sql-postgres.md`, `spanner.md`) — per-DB required fields and YAML template.

## Gotchas
- **Quota project vs ADC project:** ADC infers a default project from `gcloud config`, but Context Store requires an explicit quota project via `X-Goog-User-Project`. Missing quota project → 400 from Context Store API.
- **Context Store uploads and deletes are asynchronous:** `upload_context_set` / `delete_context_set` return an operation, not a finished result. Downstream skills must poll `get_operation` until `done: true` (see the Context Store (OneMCP) Protocol in `context-engineering-workflow`). The preflight probe above uses `get_context_set`, which is synchronous, precisely so it needs no polling.
- **Context Store endpoint is pinned in code:** the OneMCP endpoint the tools talk to is set in `context_set_mcp_client.py`, not in `tools.yaml`. A probe that fails with a connection or DNS error points at the endpoint, not at the user's configuration.
- **MCP restart required for new tools.yaml sources:** Toolbox reads `tools.yaml` at MCP-server startup. Validation runs standalone, but agent visibility of new sources needs a restart.
- **AlloyDB requires `cluster` + `instance`; Cloud SQL only `instance`.**
- **Spanner uses ADC; verification fails without `gcloud auth application-default login`.**
- **Evalbench cold-cache:** first `uvx google-evalbench@1.10.0` can take minutes to download; verifying `Evalbench reachable` warms the cache so downstream `evaluate` runs are fast.
