---
name: context-engineering-init
description: Ensure the environment is ready for context-engineering work — manage the Toolbox `tools.yaml` for database connections, verify runtime and GCP setup (uv, evalbench, ADC, Dataplex/GDA APIs, IAM), initialise the experiment `state.md` (Context Set coordinates and run preferences), and diagnose readiness failures raised by other skills.
---

# Skill: Environment & Connection Setup

## Goal
Ensure the caller's environment is ready for context-engineering work: Toolbox `tools.yaml` in place with at least one verified DB source, the experiment `state.md` initialised with the Context Set coordinates and run preferences that downstream skills read instead of re-asking, and (when asked or when downstream failures need diagnosis) runtime + GCP readiness verified. Manage `tools.yaml` on request; run any subset of checks on request.

## Prerequisites
- `gcloud` CLI on PATH.
- (Optional) Target GCP project id — if not provided, use ADC's default project.
- (Optional) Existing `tools.yaml` to amend rather than overwrite.

## Guidance

Pick the flow that matches the user's intent:

### Workspace layout (read first)

- `.context-engineering/tools.yaml` — the **single, shared** Toolbox configuration for the workspace. It may hold several DB sources; it is user-owned and never copied or duplicated. The Toolbox MCP server is launched by the client with this fixed path and reads it **only at startup**.
- `.context-engineering/experiments/<experiment_name>/` — one directory per experiment (the *experiment root*): `state.md`, `dataset/`, `v<N>/`, `holdout_eval/`, `final_evaluation_report.md`. `state.md`'s `## Active Database` records **which `tools.yaml` source (and its tools) this experiment uses**, so several experiments can share one `tools.yaml` while each stays self-describing.

### Manage `tools.yaml` (DB connections)

Three sub-workflows; all operate on `.context-engineering/tools.yaml`.

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
1. Read `.context-engineering/tools.yaml`. If missing, tell the user.
2. Parse and list all names under `sources:`.

Validate the target source(s) standalone (Example: `uvx toolbox-server@1.10.0 --config .context-engineering/tools.yaml invoke <source>-list-schemas`) — no MCP restart needed for validation. On validation failure, drop into the checks below to diagnose (e.g., `DB source reachable`, `ADC configured`).

**Toolbox restart rule (always state it explicitly).** The `toolbox` MCP server only reads `tools.yaml` when it starts, so the agent's `<source>-*` tools do not change until it is restarted. Tell the user to restart it:
- after **every** write this skill makes to `tools.yaml`;
- whenever the user says they edited `tools.yaml` themselves;
- whenever a source listed in `tools.yaml` has no matching `<source>-list-schemas` / `<source>-execute-sql` tool in the agent's tool list (the tell-tale sign of a stale server).

Restart instructions per client:
- Gemini CLI: `/mcp reload`
- Claude Code: `/mcp` → `toolbox` → Reconnect (or `/quit` and relaunch)
- Antigravity CLI: `/mcp` → `toolbox` → Restart

### Initialise the experiment `state.md`

Run this when a user is starting a hill-climb experiment, or when a downstream skill routes here because `.context-engineering/experiments/<experiment_name>/state.md` is missing or incomplete. Everything below is asked **once**; downstream skills (`dataset-generation`, `bootstrap`, `hillclimb`, `evaluate`, `workflow`) read the answers from `state.md` and never re-ask. If the file already exists, only fill in the missing bullets — do not overwrite existing values or any `## Iteration Log`. If the file already contains `## Final:`, the experiment is **finished**: do not modify it; offer the clone flow below instead.

**Clone mode** (follow-up to a finished experiment). Triggered when the user names a source experiment (e.g. *"cloned from `<source>` for context fixes / dataset expansion"*, usually via the hand-off card printed by `context-engineering-workflow`). Before step 1:
- Verify `.context-engineering/experiments/<source>/state.md` exists and has `## Final:`; read its `Final resource`, `## Active Database`, dataset bullets and verdict.
- **Copy exactly one file**: `<source>/dataset/golden.json` → `.context-engineering/experiments/<experiment_name>/dataset/golden.json`. Never copy `splits/`, plans or audit reports — the new experiment always produces its own split via `split_dataset` (which is deterministic, so an unchanged `golden.json` yields identical splits and comparable verdicts).
- The verdict only tells `context-engineering-dataset-generation` what to do with that file next — *context fixes* (INVESTIGATE Path A, or PASS when the user wants to push further): re-validate and split as is; *dataset expansion* (INVESTIGATE Path B or INCONCLUSIVE): expand first, then split. Record it as `Cloned from: <source> (<context fixes | dataset expansion>)`.
- Pre-fill: `Seed resource` = source's published resource (`Final resource`, if it was published; otherwise the source's best local file); `Project`/`Location` = source's; `## Active Database` = source's (same `tools.yaml` source — confirm it still exists in `tools.yaml`). Record only the `Golden dataset` bullet — the split bullets are written by the dataset skill after `split_dataset`. Skip the DB connection questions.
- `Context set id` still defaults to the **new** experiment name (step 4), so the clone is a *fresh* context set and the source's published set is never touched.

**Existing-context-set mode** (the user wants to improve a context set that is already published). Triggered when the user supplies a resource name — *"improve / fix `projects/<p>/locations/<l>/<collection>/<id>`"*. Before step 1:
- **Parse it per the Resource naming rule** in `context-engineering-workflow`: `project_id` = segment after `projects`, `location` = segment after `locations`, `context_set_id` = last segment. **Do not validate or depend on the collection segment** (`contextSets` today, `entryGroups` later — both must work unchanged). If the string lacks that structure, ask the user to re-check it; never guess.
- Confirm it exists: `get_context_set(project_id, location, context_set_id)`. NOT_FOUND → tell the user and ask for a corrected name (do not create anything). Keep the response's `display_name`/`description` for the summary.
- Record `Seed resource: <the supplied string, verbatim>`. Pre-fill `Project` and `Location` from the parsed parts (step 3 only confirms the location is in `list_context_set_locations`).
- Ask **one** question: *"Should the improved version replace `<supplied name>` (same name), or be published under a new name?"* Default for this flow: **same name**. Same name → `Context set id` = parsed `<context_set_id>` and `Final resource` = the supplied string verbatim (step 5 will then find it exists and set up the draft strategy). New name → continue with step 4 as usual.
- `v0` comes from the seed, not from bootstrap (hillclimb downloads it).

1. **Experiment name** — ask for a short slug (e.g. `retail-graph`). The experiment root is `.context-engineering/experiments/<experiment_name>/`; create it. If `.context-engineering/tools.yaml` does not exist yet, run **Create new** above before continuing; if it exists with several sources, ask which one this experiment targets.
2. **Project** — the GCP project id the context set is published in (default: ADC's project). At the same time read the **ADC quota project** (`quota_project_id` in `~/.config/gcloud/application_default_credentials.json`) and tell the user plainly: *"Context Set API calls are authorised and billed against your ADC quota project `<quota>` (sent as `X-Goog-User-Project`); the context set itself will live in `<project>`."* If the quota project is missing, stop and give the fix (`gcloud auth application-default set-quota-project <project>`); if it differs from `<project>`, say so and continue only if the user confirms that is intended.
3. **Location** — call `list_context_set_locations(project_id)` and let the user pick from the returned list (if they named one, confirm it is in the list). Do not assume a default.
4. **Context set id** — the name the **final** context set is published under. Default: the experiment name. Derive `Final resource` strictly per the **Resource naming rule** in `context-engineering-workflow` (verbatim user string when one was supplied) — do not hardcode a path shape here.
5. **Existence probe → working-copy strategy.** Probe the bare id with `get_context_set(project_id=<project>, location=<location>, context_set_id=<context_set_id>)` and record the result as the `Draft resource` bullet; this single bullet tells hillclimb how to work:
   - **NOT_FOUND (fresh context set)** → record `Draft resource: (none — new context set; iterations upload to the final name)`. Tell the user: *"`<Final resource>` doesn't exist yet, so I'll build it in place: every iteration is uploaded under that name and the best one stays there at the end. No scratch copy, nothing to delete."*
   - **Exists** → record `Draft resource: <Final resource with its last segment replaced by <context_set_id>_draft>` (same collection segment). Tell the user — this is the only warning they get before the end: *"`<Final resource>` already exists. I will not touch it during the run: iterations go to `<Draft resource>`. At the end I'll run the holdout test on the draft, show you the current vs new results, and **ask** before replacing `<Final resource>` — and only if the new one is better. Either way the draft is deleted afterwards (expect one `delete_context_set` call)."* If the user did **not** supply this resource as their seed (they just reused a name), also offer to pick a different `Context set id` instead; if they choose another name, re-probe.
6. **Seed resource** (optional; pre-filled in clone and existing-context-set modes) — an existing Context Set resource name to start from, recorded **verbatim** as `Seed resource: <resource name>` (parsed per the Resource naming rule; any collection segment accepted). Hillclimb downloads it once as `v0` and never writes to it during the loop. Otherwise record `Seed resource: (none — seeded from bootstrap)` or `Seed resource: (local file: <path>)`.
7. **Upload approval** — ask once: *"Should I pause for your approval before each iteration's upload to the Context Set server (default), or upload automatically?"* **Default is `false`**: record `Auto-approve uploads: false` unless the user explicitly opts into automatic uploads, in which case record `Auto-approve uploads: true`. If the user does not answer or is unsure, record `false`. Make clear this covers iteration uploads only — replacing an *existing* context set at the end is always confirmed separately.
8. **Loop parameters** — **none.** Stopping rules (tuning target, plateau, iteration cap) are internal constants of `context-engineering-hillclimb`; do not ask about them and do not record them in `state.md`. `state.md` only ever records the *outcome* (`## Converged: <reason>`).
9. **Enrichment sources** (optional) — design docs, application code paths, or notes that `bootstrap` should read. Record as `Enrichment sources: [...]`.
10. **Active database** — from `.context-engineering/tools.yaml`, record under `## Active Database` the Toolbox `<source>` this experiment targets: **Source Name**, **Type**, and **Tools** (the exact tool names declared for that source, e.g. `financial-list-schemas`, `financial-execute-sql`), so the experiment is self-describing even when `tools.yaml` holds several sources. For Spanner GoogleSQL, call `<source>-list-graphs` and record the property graph ids as `- **Graph Ids**: [...]` (`[]` if none) — `generate_evalbench_configs` reads this bullet. Before recording, confirm the `<source>-list-schemas` tool is visible in the agent's tool list; if the source is in `tools.yaml` but its tools are not visible, apply the Toolbox restart rule above first.
11. **Existing dataset** — the only dataset file a user (or a clone) can bring in is the golden set, and it must live at **`.context-engineering/experiments/<experiment_name>/dataset/golden.json`**. If the user has their own evaluation dataset, tell them that exact path (offer to copy their file there — never reference it from elsewhere). If it is present, record the `Golden dataset` bullet only; otherwise leave the dataset bullets out. **Never record `Hillclimb dataset` / `Holdout dataset` here** — the hillclimb/holdout split is always produced by `context-engineering-dataset-generation` via `split_dataset`, and that skill writes those two bullets. If a user hands over pre-split files (`dev.json` / `test.json`, or a hand-made `splits/`), explain that splits cannot be supplied: they should provide the single golden set and the skill will split it (any pre-existing `dataset/splits/` not produced by `split_dataset` in this experiment is ignored and regenerated).

Write the file in the format shown in `context-engineering-hillclimb/references/workspace.md` (a `## Metadata` section with the bullets above, then `## Active Database`). Confirm the path and the recorded values back to the user in one short summary.

### Verify environment (broad or scoped)

Run any subset of the checks below. Report `PASS` or `FAIL` per check. For any `FAIL`, propose a fix and ask the user for consent before executing anything mutating (installing packages, enabling APIs, changing IAM, writing files).

- **Broad verification** ("am I ready?") → run all checks.
- **Scoped diagnosis** (a downstream skill failed) → run only the checks whose `— required by …` line references the failing operation.

## Checks

Commands in parentheses are examples — the agent may use its own approach.

### Environment
- **`uv` installed** — required to run Toolbox and Evalbench via `uvx`. (Example: `uv --version`; install via `curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`.)
- **Evalbench reachable** — required by `context-engineering-evaluate`; verifying also warms the uvx cache so the first `evaluate` run is fast. (Example: `uvx google-evalbench@1.17.0 --help`.)

### GCP authentication
- **ADC configured** — required by every GCP API call (Context Set server, QueryData, Dataplex). (Example: `gcloud auth application-default print-access-token`; fix via `gcloud auth application-default login`.)
- **ADC quota project set** — required by Context Set server; the `X-Goog-User-Project` header is derived from it. Missing → 400 on upload/download. (Example: check `quota_project_id` in `~/.config/gcloud/application_default_credentials.json`, e.g. `python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/gcloud/application_default_credentials.json'))).get('quota_project_id'))"`; fix via `gcloud auth application-default set-quota-project <project>`.)

### GCP API enablement
- **Dataplex API** (`dataplex.googleapis.com`) — required by every Context Set tool (`list_context_set_locations`, `upload_context_set`, `get_context_set`, `delete_context_set`, `get_operation`). See the Context Set (OneMCP) Protocol in `context-engineering-workflow` for how these are used.
- **Gemini Data Analytics API** (`geminidataanalytics.googleapis.com`) — required by QueryData (used inside `context-engineering-evaluate`).

(Example: check enablement via `gcloud services list --enabled --project=<project>`; enable via `gcloud services enable <api> --project=<project>`.)

### GCP IAM (operational probes)
- **Context Set server connected** — the five Context Set tools (`list_context_set_locations`, `upload_context_set`, `get_context_set`, `delete_context_set`, `get_operation`) are served by the **remote OneMCP Context Set server** registered in the plugin manifests under the key `contextmgmt` (Google-credentials auth). Check that all five appear in your tool list. If they do not, do not continue the preflight: tell the user the remote server is not connected and give the fix — ADC present with a quota project (checks above), then reload MCP servers in the client (Antigravity / Jetski: open the MCP panel; Gemini CLI: `/mcp refresh`). A `401`/`UNAUTHENTICATED` on the first call to any of these tools means the client could not mint an ADC token: re-run `gcloud auth application-default login`, then reload.
- **Context Set access** — required by every Context Set tool and by QueryData's context lookup. Probe by calling `get_context_set(project_id=<project>, location=us-central1, context_set_id=preflight-probe-does-not-exist)` — a well-formed identity that is known not to exist. Interpret the outcome:
  - **NOT_FOUND** (or INVALID_ARGUMENT) → the request was authenticated and authorized and reached the API; the probe **passes**. This is the expected result.
  - **UNAUTHENTICATED** → ADC is missing or expired; point the user back to the GCP authentication checks above.
  - **PERMISSION_DENIED** → surface the error verbatim and ask the user to request the appropriate Context Set IAM role from their IAM admin.
  - Anything else → surface the error verbatim; do not guess at the cause.
- **GDA access** — required by QueryData (used by `evaluate`). Probe by attempting a lightweight QueryData call in `<project>`. On 403, same handling.

### Toolbox configuration
- **`tools.yaml` present** — required by every skill that reads database schemas (`bootstrap`, `evaluate`, `hillclimb`). Check `.context-engineering/tools.yaml` (the fixed path the Toolbox MCP server reads). Missing → run the Create sub-workflow above.
- **Toolbox server in sync with `tools.yaml`** — required for the agent to see the sources. For every `kind: source` in `tools.yaml`, the corresponding `<source>-list-schemas` / `<source>-execute-sql` tools must be present in the agent's tool list. Any source present in the file but absent from the tool list → the server was started before that source was added (or the user edited the file by hand): **tell the user to restart the `toolbox` MCP server** (instructions above) and re-check.
- **Experiment source still configured** — required by every skill running inside an experiment. The **Source Name** under `## Active Database` in the experiment's `state.md` must exist in `tools.yaml`. Missing → the user removed or renamed it; ask them to restore it (or add it back via the Add sub-workflow) rather than silently picking another source.
- **DB source reachable** — required by any Toolbox invocation on that source. For each configured `<source>` in `tools.yaml`, verify Toolbox can list its schemas standalone. On failure, surface the error verbatim; common causes are ADC, wrong project/region, DB IAM, or network. (Example: `uvx toolbox-server@1.10.0 --config .context-engineering/tools.yaml invoke <source>-list-schemas`.)

## Rules
- Never execute mutating actions (`gcloud services enable`, `gcloud projects add-iam-policy-binding`, package installs, file writes) without explicit user consent — surface the exact command and let the user run it, or ask consent before running.
- ADC only for DB auth. Never write username/password into `tools.yaml`.
- `tools.yaml` always lives at `.context-engineering/tools.yaml` — one shared file per workspace; do not offer or accept a different path and never copy it into an experiment. If the file exists, ask whether to append or overwrite. Every write ends with the Toolbox restart reminder.
- Each experiment's `state.md` records the `tools.yaml` source and tools it uses (`## Active Database`); downstream skills take the source from there, never by guessing from `tools.yaml`.
- A finished experiment (`## Final:` in `state.md`) is read-only: never change its `dataset/` or `state.md`; clone it instead.
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
- **Quota project vs ADC project:** ADC infers a default project from `gcloud config`, but Context Set server requires an explicit quota project via `X-Goog-User-Project`. Missing quota project → 400 from Context Set API.
- **Context Set uploads and deletes are asynchronous:** `upload_context_set` / `delete_context_set` return an operation, not a finished result. Downstream skills must poll `get_operation(project_id, location, operation_id)` until `done: true`, waiting ≥5 s between polls (see the Context Set (OneMCP) Protocol in `context-engineering-workflow`). The preflight probe above uses `get_context_set`, which is synchronous, precisely so it needs no polling.
- **Context Set endpoint lives in the plugin manifests, not in `tools.yaml`:** the remote server URL is the `contextmgmt` entry in `plugin/mcp_config.json` / `gemini-extension.json`. A probe that fails with a connection or DNS error points at that URL or at the client's MCP configuration, not at the user's `tools.yaml`. The client mints the bearer token from ADC and sets `X-Goog-User-Project` from the ADC quota project, which is why the quota-project check above matters.
- **MCP restart required whenever `tools.yaml` changes — by this skill or by the user:** the `toolbox` MCP server reads `.context-engineering/tools.yaml` only at startup. Standalone validation (`uvx toolbox-server … invoke`) sees the new file immediately, which makes the stale server easy to miss. Symptoms: a source that is in `tools.yaml` has no `<source>-list-schemas` tool, or a tool still reflects a connection value that the user has since edited. Fix: restart the `toolbox` server with the per-client instructions above, then re-run the *Toolbox server in sync* check.
- **AlloyDB requires `cluster` + `instance`; Cloud SQL only `instance`.**
- **Spanner uses ADC; verification fails without `gcloud auth application-default login`.**
- **Evalbench cold-cache:** first `uvx google-evalbench@1.17.0` can take minutes to download; verifying `Evalbench reachable` warms the cache so downstream `evaluate` runs are fast.
