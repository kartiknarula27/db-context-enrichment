---
name: context-engineering-hillclimb
description: Autonomously improve a ContextSet by iterating evaluate → analyze → mutate → re-upload until convergence, given a golden dataset and (optionally) a base context. The user supplies inputs once; no per-iteration approval needed.
---

> **Load [`context-engineering-workflow`](../context-engineering-workflow/SKILL.md) first** for shared terminology, lifecycle overview, and safety protocol.

# Skill: Automated Hill-Climbing

## Goal
Given a golden dataset and (optionally) a base context, autonomously produce a high-quality ContextSet by iterating evaluate → analyze → mutate → re-upload until convergence. The user supplies inputs once and receives the final high-scoring ContextSet; no per-iteration approval is required.

## Prerequisites
- A working DB connection — Toolbox MCP tools (`<source>-list-schemas`, `<source>-execute-sql`) must be visible to the agent throughout the run. If missing or unreachable at any point, stop and route through `context-engineering-init`; do not work around it (no bash `uvx toolbox-server invoke` fallback).
- A golden evaluation dataset (JSON, simplified format: `{id, database, nlq, golden_sql}` — see `context-engineering-evaluate`).
- ADC configured and the Gemini Data Analytics + Dataplex APIs enabled on the project (see `context-engineering-init` for preflight).
- A starting context — none, a local file, or an existing Context Store resource name. Entry flow spells out the handling per case.
- Context Store coordinates for this run: `project_id`, `location` (confirmed via `list_context_set_locations`), and a `context_set_id` — the name the **final** context set will be published under. Default `context_set_id` = experiment name. Per-iteration drafts are uploaded as `<context_set_id>_draft<N>` and deleted by the loop; see the Context Store (OneMCP) Protocol in `context-engineering-workflow`.
- (Optional) Workspace root directory. Default: `.context-engineering/experiments/<experiment_name>/` at cwd.

## Guidance

Load `references/workspace.md` before any workspace interaction — it describes the internal iteration layout.

### How the Context Store is used in this loop

Local files are the source of truth. Every iteration's ContextSet lives at `vN/context_set_vN.json` on disk and stays there. The Context Store holds at most **one transient draft** at a time: iteration `N` uploads `<context_set_id>_draftN`, evaluates against it, and deletes it as soon as the score is recorded. Nothing is ever re-downloaded from the store mid-run. When the loop converges, the best iteration's local file is uploaded once more under the bare `<context_set_id>` — that is the deliverable.

Every `upload_context_set` and `delete_context_set` below is followed by the poll gate: poll `get_operation` on the returned operation (1 s doubling backoff, 5-minute ceiling) until `done: true` before continuing. See the Context Store (OneMCP) Protocol.

### Entry flow
1. **Locate workspace** at `<workspace_root>` (default `.context-engineering/experiments/<experiment_name>/`). Confirm the path with the user before creating it fresh — the directory holds every iteration's scratch state. If `state.md` exists → **resume** from the recorded iteration (see Resume rules in `references/workspace.md`). Otherwise → **fresh start**.
2. **Resolve Context Store coordinates.** Collect `project_id`, `location`, `context_set_id`. Call `list_context_set_locations(project_id)` and confirm `location` is in the list (if not, let the user pick). Show the user the final resource name `projects/<project_id>/locations/<location>/contextSets/<context_set_id>` and the draft pattern `<context_set_id>_draft<N>`. Record all of this in `state.md`.
3. **Fresh start — seed v0.** Produce `v0/context_set_v0.json` per the base-context case:
   - **None** → invoke `context-engineering-bootstrap` to generate the file at `v0/context_set_v0.json` (no upload from bootstrap; this loop handles uploads).
   - **Local file** → copy it to `v0/context_set_v0.json`.
   - **Existing Context Store resource name** → call `get_context_set` on it, parse `payload`, and write it to `v0/context_set_v0.json`. **This resource is user-owned: the loop never deletes or overwrites it.** Use it directly as the baseline's `cs_resource_name` (skip the upload/delete in the next step for `v0` only).

   Then baseline-evaluate `v0`: unless seeded from an existing resource, upload `v0/context_set_v0.json` as `<context_set_id>_draft0` → poll gate → evaluate into `v0/eval/` → record the baseline score → `delete_context_set(<context_set_id>_draft0)` → poll gate. Write the `### v0` entry to `state.md`. Iteration loop starts at `v1`.

### Per-iteration loop (`vN`)
1. **Prepare `vN/`**: append the `## In-Progress: vN` marker to `state.md`, create the iteration directory, and seed `context_set_vN.json` by copying `v(N-1)/context_set_v(N-1).json` from disk. The previous draft no longer exists in the store, so there is no remote fallback: if the local file is missing (crash), copy the most recent `vK/context_set_vK.json` that does exist; if none exists, re-run the entry-flow seed.
2. **Analyze the previous evaluation**: read `v(N-1)/eval/` via `read_evaluation_result`; cluster failures by category; write findings + reasoning + planned mutations to `analysis_vN.md`.
   - **Pagination**: The tool returns a batch of failure cases (default limit 10). If there are many failures, iterate by calling the tool with increasing `offset` (0, 10, 20...) until all failed queries are analyzed.
   - **Use `pipeline_debug_info`**: If a failure case's Additional Output contains it, use this generation trace (showing which context the API retrieved and used) to ground the root cause (e.g., distinguishing a missing template match vs. a bad column reference) rather than guessing.
3. **Mutate**: plan mutations from the analysis (prefer fewer general items — a facet often beats many templates). Author new items via `context-engineering-generation-guide`. Validate generated SQL via `<source>-execute-sql` and column references via `<source>-list-schemas`. Apply via `mutate_context_set` to `context_set_vN.json`.
4. **Upload draft**: `upload_context_set(context_set=projects/<project_id>/locations/<location>/contextSets/<context_set_id>_draftN, local_file_path=vN/context_set_vN.json)` → poll gate until `done: true`. Record the draft resource name and operation name in `state.md`.
5. **Evaluate**: invoke `context-engineering-evaluate` with `cs_resource_name=<the draft resource>` and `output_dir=<workspace>/vN/eval/`. Capture `job_id` and overall score.
6. **Sweep the draft**: as soon as the score is recorded, `delete_context_set(<the draft resource>)` → poll gate until `done: true`. This is unconditional — the draft is deleted whether or not it improved. The local `vN/` directory is kept.
7. **Update `state.md`**: replace the `## In-Progress: vN` marker with the final `### vN` entry (local file, eval report path, analysis path, score, draft upload/delete operation names, `Draft: deleted`).
8. **Check convergence** (see below). If not converged, continue to `v(N+1)`.

### Convergence and finalization
Iterate until you don't see new improvements. When stopping, append `## Converged: <one-line reason>` to `state.md` so a resume knows the run terminated intentionally.

The user can also explicitly ask to stop at any time; the current iteration completes cleanly (including the draft sweep), `## User Stop` is appended to `state.md`, and all iteration files are preserved.

**Finalize** (runs after `## Converged` or `## User Stop`):
1. Pick the best-scoring iteration `K` from the Iteration Log.
2. Show the user the final resource name `projects/<project_id>/locations/<location>/contextSets/<context_set_id>` and ask them to confirm. Make the overwrite risk explicit: if a context set with this exact name already exists — from an earlier run, a teammate, or a production deployment — this upload **replaces its contents and the old contents cannot be recovered**. Offer to use a different `context_set_id` if they are unsure.
3. On confirmation, append `## Finalizing: vK` to `state.md`, then `upload_context_set(context_set=<final resource>, local_file_path=vK/context_set_vK.json, description=<experiment + score>)` → poll gate until `done: true`.
4. Replace `## Finalizing` with `## Final: <final resource> (from vK, score <S>)` in `state.md`.

**Final output** is the bare `<context_set_id>` resource name, its score, and the local file it was uploaded from.

## Rules
- The workspace is internal state; user-visible output is the final `cs_resource_name` and score. Do not surface intermediate iteration files as user deliverables.
- Only `Template`, `Facet`, `Value Search` types are emitted as mutations. Do not invent new item types.
- Avoid overfitting: mutations should generalize to unseen NLQs, not just fix specific failing golden pairs.
- Compose the sibling skills (`context-engineering-bootstrap`, `context-engineering-evaluate`, `context-engineering-generation-guide`); never re-implement their logic.
- Never require per-iteration user approval — the loop must run autonomously. Only pause on: convergence, error, or explicit user stop.
- Do not read the target ContextSet file directly for mutations; always use `mutate_context_set`.
- A full run is long — 10 iterations × multi-minute evaluations each can take 30–60+ minutes. Warn the user upfront and run in a session that tolerates long work.
- When updating `state.md`, preserve the existing `## Active Database` section and append or update the `Hill-Climbing Run Log` section
- Never evaluate a draft whose upload operation has not reported `done: true`; never start the next iteration until the previous draft's delete operation has reported `done: true`.
- Only delete resources this run created: names ending in `_draft<N>` that are recorded in this run's `state.md`. Never delete a user-supplied seed resource or anything under the bare `<context_set_id>`.
- Local `vN/context_set_vN.json` files are the only durable record of each iteration. Never rely on the Context Store to recover a prior iteration.
- The final upload under the bare `<context_set_id>` always requires explicit user confirmation, because it overwrites any existing context set of that name.

## Tools

**Sibling skills:**
- `context-engineering-bootstrap` — cold-start path when no base context.
- `context-engineering-evaluate` — invoked once per iteration.
- `context-engineering-generation-guide` — produces well-formed Template / Facet / Value Search JSON.

**MCP:**
- `list_context_set_locations` — confirm the run's location once at entry.
- `upload_context_set` — push each `_draftN` and, at the end, the final `<context_set_id>`; returns an operation.
- `delete_context_set` — sweep each `_draftN` after its evaluation; returns an operation.
- `get_operation` — poll every upload/delete operation until `done: true`.
- `get_context_set` — seed `v0` from a user-supplied existing resource (entry flow only).
- `mutate_context_set` — apply planned mutations.
- `read_evaluation_result` — read scored eval reports.
- `<source>-list-schemas` (Toolbox) — validate that referenced columns exist.
- `<source>-execute-sql` (Toolbox) — validate that generated SQL runs against the DB.

**References:**
- `references/workspace.md` — internal workspace layout; load before any workspace interaction.
