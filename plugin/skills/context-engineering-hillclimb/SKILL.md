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
- A golden evaluation dataset split by `context-engineering-dataset-generation`, stored at the DB level (`.context-engineering/golden.json`, `.context-engineering/splits/hillclimb.json`, `.context-engineering/splits/holdout.json`) and recorded in `state.md` — the loop evaluates on `splits/hillclimb.json` only; `splits/holdout.json` is never read until after publish (see the Holdout Evaluation phase in `context-engineering-workflow`).
- ADC configured and the Gemini Data Analytics + Dataplex APIs enabled on the project (see `context-engineering-init` for preflight).
- A starting context — none, a local file, or an existing Context Store resource name. Entry flow spells out the handling per case.
- An experiment `state.md` written by `context-engineering-init` at `.context-engineering/experiments/<experiment_name>/state.md`. It holds the Context Store coordinates (`project_id`, `location`, `context_set_id` — the name the **final** context set is published under), the upload-approval preference, and the loop parameters. If it is missing, route to `context-engineering-init` first. Every iteration overwrites one working copy, `<context_set_id>_draft`; it is deleted once, after publish — see the Context Store (OneMCP) Protocol in `context-engineering-workflow`.
- (Optional) Workspace root directory. Default: `.context-engineering/experiments/<experiment_name>/` at cwd.

## Guidance

Load `references/workspace.md` before any workspace interaction — it describes the internal iteration layout.

### How the Context Store is used in this loop

Local files are the source of truth. Every iteration's ContextSet lives at `vN/context_set_vN.json` on disk and stays there. The Context Store holds **one working copy** for the whole run: `<context_set_id>_draft`. Iteration `N` overwrites it with `vN/context_set_vN.json`, evaluates against it, and moves on — there is no per-iteration delete. Nothing is ever re-downloaded from the store mid-run. When a stopping condition fires, the best iteration's local file is uploaded under the bare `<context_set_id>` — that is the deliverable — and the working copy is deleted once.

Every `upload_context_set` and `delete_context_set` below is followed by the poll gate: poll `get_operation(project_id, location, operation_id)` on the returned operation (≥5 s between polls, 5-minute ceiling) until `done: true` before continuing. `upload_context_set` takes the ContextSet **inline** in `context_payload` — read the local file and pass its exact contents; there is no file-path argument. Every store tool identifies the context set by `project_id`, `location`, and `context_set_id` (read `Project` and `Location` from `state.md`; the draft's `context_set_id` is `<context_set_id>_draft`). See the Context Store (OneMCP) Protocol in `context-engineering-workflow`.

**Upload approval — two tiers.** `state.md` records `Auto-approve uploads` (asked once by init).
- `true` → every `_draft` upload below proceeds without asking.
- `false` → before each `_draft` upload, pause and say: *"I've made improvements to the context set in `vN/context_set_vN.json`. Please approve the changes, and I'll upload it for evaluation."* Wait for approval; if the user edits the file, re-run `validate_context_set` before uploading.
The publish to the bare id never asks: the overwrite was acknowledged in init (`Overwrite acknowledged: yes`).

### Entry flow
1. **Locate workspace** at `<workspace_root>` (default `.context-engineering/experiments/<experiment_name>/`). If `state.md` is missing → route to `context-engineering-init` (it creates the workspace and `state.md`). If `state.md` has an `## Iteration Log` → **resume** from the recorded iteration (see Resume rules in `references/workspace.md`). Otherwise → **fresh start**.
2. **Read Context Store coordinates from `state.md`** (`Project`, `Location`, `Context set id`, `Final resource`, `Draft resource`, `Seed resource`, `Auto-approve uploads`, `Tuning target`, `Plateau k`, `Max iterations`, `Hillclimb dataset`). Do not re-ask for any of them; if one is missing, route to `context-engineering-init` to fill it in.
3. **Fresh start — seed v0.** Produce `v0/context_set_v0.json` per the base-context case:
   - **None** → invoke `context-engineering-bootstrap` to generate the file at `v0/context_set_v0.json` (no upload from bootstrap; this loop handles uploads). Bootstrap reads enrichment sources and scope from `state.md`.
   - **Local file** → copy it to `v0/context_set_v0.json`.
   - **Existing Context Store resource name** (`Seed resource` in `state.md`) → split it into `project_id`, `location`, `context_set_id`, call `get_context_set(project_id, location, context_set_id)`, parse `payload`, and write it to `v0/context_set_v0.json`. **This resource is user-owned: the loop never deletes or overwrites it.**

   Then baseline-evaluate `v0`: upload `v0/context_set_v0.json` to `<context_set_id>_draft` (approval tier applies) → poll gate → evaluate into `v0/eval/` on the `Hillclimb dataset` from `state.md` (`.context-engineering/splits/hillclimb.json`) → record the baseline score. Write the `### v0` entry to `state.md`. Iteration loop starts at `v1`.

### Per-iteration loop (`vN`)
1. **Prepare `vN/`**: append the `## In-Progress: vN` marker to `state.md`, create the iteration directory, and seed `context_set_vN.json` by copying `v(N-1)/context_set_v(N-1).json` from disk. Local files are the only durable record, so there is no remote fallback: if the local file is missing (crash), copy the most recent `vK/context_set_vK.json` that does exist; if none exists, re-run the entry-flow seed.
2. **Analyze the previous evaluation**: read `v(N-1)/eval/` via `read_evaluation_result`; cluster failures by category; write findings + reasoning + planned mutations to `analysis_vN.md`.
   - **Pagination**: The tool returns a batch of failure cases (default limit 10). If there are many failures, iterate by calling the tool with increasing `offset` (0, 10, 20...) until all failed queries are analyzed.
   - **Use `pipeline_debug_info`**: If a failure case's Additional Output contains it, use this generation trace (showing which context the API retrieved and used) to ground the root cause (e.g., distinguishing a missing template match vs. a bad column reference) rather than guessing.
3. **Mutate**: plan mutations from the analysis (prefer fewer general items — a facet often beats many templates). Author new items via `context-engineering-generation-guide`. Validate generated SQL via `<source>-execute-sql` and column references via `<source>-list-schemas`. Apply via `mutate_context_set` to `context_set_vN.json`.
4. **Upload the working copy** (approval tier applies): read `vN/context_set_vN.json` and call `upload_context_set(project_id=<Project>, location=<Location>, context_set_id=<context_set_id>_draft, context_payload=<exact file contents>)` → poll gate until `done: true`. This overwrites the previous iteration's copy. Record the operation name in `state.md`.
5. **Evaluate**: invoke `context-engineering-evaluate` with `cs_resource_name=<the _draft resource>`, the `Hillclimb dataset` path from `state.md` (`.context-engineering/splits/hillclimb.json`), and `output_dir=<workspace>/vN/eval/`. Capture `job_id`, `passed`, `total` and overall score.
6. **Update `state.md`**: replace the `## In-Progress: vN` marker with the final `### vN` entry (local file, eval report path, analysis path, score, upload operation name).
7. **Check the stopping conditions** (see below). If none fired, continue to `v(N+1)`.

### Stopping conditions and finalization
After every evaluation, check — in this order — the conditions recorded in `state.md`:

| Condition | Fires when | `## Converged:` reason |
| :--- | :--- | :--- |
| Tuning target | `passed / total >= Tuning target` (default `1.0`, i.e. zero failures) | `Tuning target <t> reached.` |
| Plateau | the last `Plateau k` iterations (default `3`) all failed to beat the best score so far | `Plateau reached over <k> consecutive mutations.` |
| Iteration cap | `Max iterations` (default `10`) iterations `v1..vN` have been evaluated | `Maximum iterations (<n>) reached.` |

When one fires, append `## Converged: <reason>` to `state.md` and run **Finalize** immediately, in the same turn, without asking.

The user can also explicitly ask to stop at any time; the current iteration completes cleanly, `## User Stop` is appended to `state.md`, and all iteration files are preserved. A user stop does **not** publish and does **not** run the holdout test — a later resume offers the choice.

**Finalize** (runs after `## Converged`, or after `## User Stop` when the user asks to publish):
1. Pick the best-scoring iteration `K` from the Iteration Log (ties → earliest).
2. Append `## Finalizing: vK` to `state.md`, then read `vK/context_set_vK.json` and call `upload_context_set(project_id=<Project>, location=<Location>, context_set_id=<context_set_id>, context_payload=<exact file contents>, description=<experiment + score>)` → poll gate until `done: true`. No confirmation is asked here — the overwrite was acknowledged once in init.
3. `delete_context_set(project_id=<Project>, location=<Location>, context_set_id=<context_set_id>_draft)` → poll gate until `done: true`. NOT_FOUND counts as success (a resumed run may have already cleaned up).
4. Replace `## Finalizing` with `## Final: <final resource> (from vK, score <S>)` plus the two operation names in `state.md`.
5. Print the publish confirmation (resource name, `vK`, score, local file), then **hand over to the Holdout Evaluation & Generalization Reporting Phase** in `context-engineering-workflow` — still in the same turn.

**Final output** is the bare `<context_set_id>` resource name, its score, the local file it was uploaded from, and the generalizability verdict.

## Rules
- The workspace is internal state; user-visible output is the final `cs_resource_name` and score. Do not surface intermediate iteration files as user deliverables.
- Only `Template`, `Facet`, `Value Search` types are emitted as mutations. Do not invent new item types.
- Avoid overfitting: mutations should generalize to unseen NLQs, not just fix specific failing golden pairs.
- Compose the sibling skills (`context-engineering-bootstrap`, `context-engineering-evaluate`, `context-engineering-generation-guide`); never re-implement their logic.
- Never require per-iteration user approval — the loop must run autonomously. Only pause on: error, explicit user stop, or — when `state.md` says `Auto-approve uploads: false` — the pre-upload approval of each `_draft`. Convergence does not pause: Finalize and the holdout test run in the same turn.
- Do not read the target ContextSet file directly for mutations; always use `mutate_context_set`.
- A full run is long — 10 iterations × multi-minute evaluations each can take 30–60+ minutes. Warn the user upfront and run in a session that tolerates long work.
- When updating `state.md`, preserve the existing `## Metadata` and `## Active Database` sections (written by init) and append or update the `## Iteration Log` section and markers only.
- Never evaluate a draft whose upload operation has not reported `done: true`.
- Only delete resources this run created: the single `<context_set_id>_draft` recorded in this run's `state.md`, and only after the publish upload has reported `done: true`. Never delete a user-supplied seed resource or anything under the bare `<context_set_id>`.
- Local `vN/context_set_vN.json` files are the only durable record of each iteration. Never rely on the Context Store to recover a prior iteration.
- The publish under the bare `<context_set_id>` overwrites any existing context set of that name. That overwrite is acknowledged **once**, by init (`Overwrite acknowledged: yes` in `state.md`); do not re-ask at publish time. If the line is missing or `no`, route to `context-engineering-init` before publishing.

## Tools

**Sibling skills:**
- `context-engineering-bootstrap` — cold-start path when no base context.
- `context-engineering-evaluate` — invoked once per iteration.
- `context-engineering-generation-guide` — produces well-formed Template / Facet / Value Search JSON.

**MCP — Context Store (remote OneMCP server):**
- `upload_context_set` — overwrite `<context_set_id>_draft` every iteration and, at the end, publish the best iteration under the bare `<context_set_id>`; takes the file contents inline in `context_payload`; returns an operation.
- `delete_context_set` — remove `<context_set_id>_draft` once, after the publish succeeds; returns an operation.
- `get_operation` — poll every upload/delete operation (`project_id`, `location`, `operation_id`) until `done: true`.
- `get_context_set` — seed `v0` from a user-supplied existing resource (entry flow only). Location and overwrite checks are done by `context-engineering-init`, not here.

**MCP — local plugin and Toolbox:**
- `mutate_context_set` — apply planned mutations.
- `validate_context_set` — check `vN/context_set_vN.json` before every upload, and re-check after a user edits it during the manual-approval pause (`Auto-approve uploads: false`).
- `read_evaluation_result` — read scored eval reports.
- `<source>-list-schemas` (Toolbox) — validate that referenced columns exist.
- `<source>-execute-sql` (Toolbox) — validate that generated SQL runs against the DB.

**References:**
- `references/workspace.md` — internal workspace layout; load before any workspace interaction.
