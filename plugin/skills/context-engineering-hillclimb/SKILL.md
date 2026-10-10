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
- A golden evaluation dataset split by `context-engineering-dataset-generation`, stored **inside the experiment** (`.context-engineering/experiments/<experiment_name>/dataset/golden.json`, `dataset/splits/hillclimb.json`, `dataset/splits/holdout.json`) and recorded in `state.md` — the loop evaluates on `splits/hillclimb.json` only; `splits/holdout.json` is never read until the loop has converged (see the Holdout Evaluation phase in `context-engineering-workflow`).
- ADC configured and the Gemini Data Analytics + Dataplex APIs enabled on the project (see `context-engineering-init` for preflight).
- A starting context — none, a local file, or an existing Context Set resource name. Entry flow spells out the handling per case.
- An experiment `state.md` written by `context-engineering-init` at `.context-engineering/experiments/<experiment_name>/state.md`. It holds the Context Set coordinates (`project_id`, `location`, `context_set_id` — the name the **final** context set lives under), the `Draft resource` bullet that decides the working copy (none for a fresh name, `<context_set_id>_draft` when the name already existed), the optional `Seed resource`, and the upload-approval preference. If it is missing, route to `context-engineering-init` first. See the Context Set (OneMCP) Protocol in `context-engineering-workflow`.
- (Optional) Workspace root directory. Default: `.context-engineering/experiments/<experiment_name>/` at cwd.

## Guidance

Load `references/workspace.md` before any workspace interaction — it describes the internal iteration layout.

### How the Context Set server is used in this loop

Local files are the source of truth. Every iteration's ContextSet lives at `vN/context_set_vN.json` on disk and stays there. The Context Set server holds **one working copy** for the whole run; iteration `N` overwrites it with `vN/context_set_vN.json`, evaluates against it, and moves on — there is no per-iteration delete and nothing is re-downloaded mid-run. **Which resource is the working copy depends on whether the final name already existed when init ran** (read `Draft resource` from `state.md`):

| Case | `Draft resource` in `state.md` | Working copy for `v0..vN` | End of run |
| :-- | :-- | :-- | :-- |
| **Fresh** — `<context_set_id>` did not exist at init | `(none — new context set; iterations upload to the final name)` | the bare `<context_set_id>` itself | re-upload the best `vK` to the same name, run the holdout test on it, done. No `_draft`, no delete. |
| **Existing** — `<context_set_id>` existed at init (in-place improvement of a user-supplied resource, or a reused name) | `…/<context_set_id>_draft` | `<context_set_id>_draft`; the existing set is **never written during the loop** | re-upload the best `vK` to the draft, run the holdout test on the draft, then a **mandatory HITL** (even with `Auto-approve uploads: true`): show previous vs new results and ask whether to replace the existing set — asked **only if `vK` beats `v0`**. Delete the draft afterwards either way. |

Every `upload_context_set` and `delete_context_set` below is followed by the poll gate: poll `get_operation(project_id, location, operation_id)` on the returned operation (≥5 s between polls, 5-minute ceiling) until `done: true` before continuing. `upload_context_set` takes the ContextSet **inline** in `context_payload` — read the local file and pass its exact contents; there is no file-path argument. Every store tool identifies the context set by `project_id`, `location`, and `context_set_id` (read `Project` and `Location` from `state.md`). See the Context Set (OneMCP) Protocol in `context-engineering-workflow`.

**Upload approval — two tiers for iteration uploads.** `state.md` records `Auto-approve uploads` (asked once by init; **default `false`**).
- `true` (explicit opt-in only) → every working-copy upload below proceeds without asking.
- `false`, or the bullet is missing → before each working-copy upload, pause and say: *"I've made improvements to the context set in `vN/context_set_vN.json`. Please approve the changes, and I'll upload it for evaluation."* Wait for approval; if the user edits the file, re-run `validate_context_set` before uploading. Never assume `true`.
These tiers govern **iteration** uploads only. Replacing an **existing** context set at the end is always gated by the Finalize HITL below, regardless of the tier.

### Entry flow
1. **Locate workspace** at `<workspace_root>` (default `.context-engineering/experiments/<experiment_name>/`; it holds this experiment's `state.md` and `dataset/`; the DB connection it uses is the `tools.yaml` source named under `## Active Database`). If `state.md` is missing → route to `context-engineering-init` (it creates the workspace and `state.md`, and `tools.yaml` if the workspace has none). If `state.md` has `## Final:` → the experiment is **finished and read-only**: do not iterate; report the outcome and, unless the verdict was PASS, re-print the *Next experiment hand-off* from `context-engineering-workflow`; route to `context-engineering-init` in clone mode if the user wants to continue. If `state.md` has an `## Iteration Log` → **resume** from the recorded iteration (see Resume rules in `references/workspace.md`). Otherwise → **fresh start**.
2. **Read Context Set coordinates from `state.md`** (`Project`, `Location`, `Context set id`, `Final resource`, `Draft resource`, `Seed resource`, `Auto-approve uploads`, `Hillclimb dataset`). Do not re-ask for any of them; if one is missing, route to `context-engineering-init` to fill it in. Set **`<working_copy>`** = the bare `<context_set_id>` when `Draft resource` is `(none …)` (fresh case), else `<context_set_id>_draft` (existing case). Stopping rules come from this skill's constants (below), never from `state.md` or the user.
3. **Fresh start — seed v0.** Produce `v0/context_set_v0.json` per the base-context case:
   - **None** → invoke `context-engineering-bootstrap` to generate the file at `v0/context_set_v0.json` (no upload from bootstrap; this loop handles uploads). Bootstrap reads enrichment sources and scope from `state.md`.
   - **Local file** → copy it to `v0/context_set_v0.json`.
   - **Existing Context Set resource name** (`Seed resource` in `state.md`) → parse it per the Resource naming rule in `context-engineering-workflow` (`project_id` after `projects`, `location` after `locations`, `context_set_id` = last segment; **ignore the collection segment** — `contextSets`, `entryGroups`, or whatever the service uses), call `get_context_set(project_id, location, context_set_id)`, parse `payload`, and write it to `v0/context_set_v0.json`. The loop never writes to the seed; if the seed *is* the `Final resource` (in-place improvement), it is replaced only by the Finalize HITL below, and only with the user's explicit yes.

   Then baseline-evaluate `v0`: upload `v0/context_set_v0.json` to `<working_copy>` (approval tier applies) → poll gate → evaluate into `v0/eval/` on the `Hillclimb dataset` from `state.md` (`<workspace_root>/dataset/splits/hillclimb.json`) → record the baseline score. Write the `### v0` entry to `state.md`. Iteration loop starts at `v1`. (In the existing case this upload creates the `_draft`; tell the user once: *"Iterations run on `<draft>`; `<Final resource>` stays untouched until you approve a replacement at the end, after which the draft is deleted."*)

### Per-iteration loop (`vN`)
1. **Prepare `vN/`**: append the `## In-Progress: vN` marker to `state.md`, create the iteration directory, and seed `context_set_vN.json` by copying `v(N-1)/context_set_v(N-1).json` from disk. Local files are the only durable record, so there is no remote fallback: if the local file is missing (crash), copy the most recent `vK/context_set_vK.json` that does exist; if none exists, re-run the entry-flow seed.
2. **Analyze the previous evaluation**: read `v(N-1)/eval/` via `read_evaluation_result`; cluster failures by category; write findings + reasoning + planned mutations to `analysis_vN.md`.
   - **Pagination**: The tool returns a batch of failure cases (default limit 10). If there are many failures, iterate by calling the tool with increasing `offset` (0, 10, 20...) until all failed queries are analyzed.
   - **Use `pipeline_debug_info`**: If a failure case's Additional Output contains it, use this generation trace (showing which context the API retrieved and used) to ground the root cause (e.g., distinguishing a missing template match vs. a bad column reference) rather than guessing.
3. **Mutate**: plan mutations from the analysis (prefer fewer general items — a facet often beats many templates). Author new items via `context-engineering-generation-guide`. Validate generated SQL via `<source>-execute-sql` and column references via `<source>-list-schemas`. Apply via `mutate_context_set` to `context_set_vN.json`.
4. **Upload the working copy** (approval tier applies): read `vN/context_set_vN.json` and call `upload_context_set(project_id=<Project>, location=<Location>, context_set_id=<working_copy>, context_payload=<exact file contents>)` → poll gate until `done: true`. This overwrites the previous iteration's copy. Record the operation name in `state.md`.
5. **Evaluate**: invoke `context-engineering-evaluate` with `cs_resource_name=<the working-copy resource>`, the `Hillclimb dataset` path from `state.md` (`<workspace_root>/dataset/splits/hillclimb.json`), and `output_dir=<workspace>/vN/eval/`. Capture `job_id`, `passed`, `total` and overall score.
6. **Update `state.md`**: replace the `## In-Progress: vN` marker with the final `### vN` entry (local file, eval report path, analysis path, score, upload operation name).
7. **Check the stopping conditions** (see below). If none fired, continue to `v(N+1)`.

### Stopping conditions and finalization
The stopping rules are **internal constants of this skill** — they are not asked of the user by init and are **not recorded in `state.md`** (only the outcome is). After every evaluation, check them in this order:

| Condition | Constant | Fires when | `## Converged:` reason |
| :--- | :--- | :--- | :--- |
| Tuning target | `TUNING_TARGET = 1.0` | `passed / total >= TUNING_TARGET` (i.e. zero failures) | `Tuning target <t> reached.` |
| Plateau | `PLATEAU_K = 3` | the last `PLATEAU_K` iterations all failed to beat the best score so far | `Plateau reached over <k> consecutive mutations.` |
| Iteration cap | `MAX_ITERATIONS = 10` | `MAX_ITERATIONS` iterations `v1..vN` have been evaluated | `Maximum iterations (<n>) reached.` |

Power users (and smoke tests) may override a constant **explicitly in their prompt** for the current session ("max iterations 3"); honour it, say so once, but do not write it to `state.md` — a later resume falls back to the constants unless the user restates the override. The `## Converged:` reason already carries the effective value.

When one fires, append `## Converged: <reason>` to `state.md` and run **Finalize** immediately, in the same turn, without asking. (The only pause inside Finalize is the replacement HITL of the existing case.)

The user can also explicitly ask to stop at any time; the current iteration completes cleanly, `## User Stop` is appended to `state.md`, and all iteration files are preserved. A user stop does **not** finalize and does **not** run the holdout test — a later resume offers the choice.

**Finalize** (runs after `## Converged`, or after `## User Stop` when the user asks to finalize). Order matters: the holdout test runs on the candidate **before** anything existing is replaced.

1. **Pick the candidate**: the best-scoring iteration `K` from the Iteration Log (ties → earliest). Append `## Finalizing: vK` to `state.md`.
2. **Put the candidate in the working copy**: if `K ≠ N` (the working copy holds the last iteration, not the best), read `vK/context_set_vK.json` and `upload_context_set(... context_set_id=<working_copy>, context_payload=<exact file contents>, description=<experiment + score>)` → poll gate. The re-upload is unconditional on a resume (never infer what the server holds). The working copy now *is* the candidate.
3. **Holdout test on the working copy**: hand over to the **Holdout Evaluation & Generalization Reporting Phase** in `context-engineering-workflow` with the working-copy resource as the candidate. It writes `final_evaluation_report.md` and `## Generalizability` to `state.md` and returns the verdict and the holdout score. Do not print the on-screen card yet in the existing case — the user needs the decision first.
4. **Finish per case:**
   - **Fresh case** (`<working_copy>` = `<context_set_id>`): the candidate is already live under the final name. Replace `## Finalizing` with `## Final: <Final resource> (from vK, score <S>)` plus the upload operation name. Nothing to delete.
   - **Existing case** (`<working_copy>` = `<context_set_id>_draft`) — **mandatory HITL, regardless of `Auto-approve uploads`:**
     - If `score(vK) > score(v0)` on the hillclimb split, present a before/after and ask:
       ```markdown
       ### Replace the existing context set?
       | | Context set | Hillclimbing accuracy | Holdout accuracy | Local file |
       | :-- | :-- | :-- | :-- | :-- |
       | **Current** (`<Final resource>`) | what is published today | <score(v0)> | — | `v0/context_set_v0.json` |
       | **New candidate** (`<Draft resource>`) | best of this run (`vK`) | <score(vK)> | <holdout score> (**<VERDICT>**) | `vK/context_set_vK.json` |
       Replace `<Final resource>` with the new candidate? The current contents stay on disk in `v0/context_set_v0.json` and can be re-uploaded if you change your mind.
       ```
       - **Yes** → read `vK/context_set_vK.json`, `upload_context_set(... context_set_id=<context_set_id> ...)` → poll gate → `delete_context_set(... <context_set_id>_draft)` → poll gate (NOT_FOUND = success). Replace `## Finalizing` with `## Final: <Final resource, exactly as recorded in state.md> (from vK, score <S>) — replaced previous contents` plus both operation names.
       - **No** → `delete_context_set(... <context_set_id>_draft)` → poll gate. Replace `## Finalizing` with `## Final: not published — <Final resource> unchanged; best candidate vK/context_set_vK.json (score <S>)`. Offer to publish the candidate under a new name later (that is a new experiment cloned from this one with a new `Context set id`).
     - If `vK` does **not** beat `v0` (`K = 0`, or equal score): do **not** offer a replacement. Say so plainly ("the published context set is already as good as anything this run produced"), `delete_context_set(... _draft)` → poll gate, and write `## Final: not published — no improvement over v0; <Final resource> unchanged`.
5. **Print the on-screen card** from the Holdout phase (now that the publication state is known), then the *Next experiment hand-off* **unless the verdict is PASS** — see the workflow skill. Do not rebuild resource strings anywhere in this step; reuse the ones recorded in `state.md` so the collection segment the user gave is preserved.

**Final output** is the `## Final:` line (resource name or "not published"), the candidate's score and local file, and the generalizability verdict. No `_draft` resource remains on the server when a run is complete.

## Rules
- The workspace is internal state; user-visible output is the final `cs_resource_name` and score. Do not surface intermediate iteration files as user deliverables.
- Only `Template`, `Facet`, `Value Search` types are emitted as mutations. Do not invent new item types.
- Avoid overfitting: mutations should generalize to unseen NLQs, not just fix specific failing golden pairs.
- Compose the sibling skills (`context-engineering-bootstrap`, `context-engineering-evaluate`, `context-engineering-generation-guide`); never re-implement their logic.
- Never require per-iteration user approval — the loop must run autonomously. Only pause on: error, explicit user stop, — when `state.md` says `Auto-approve uploads: false` — the pre-upload approval of each working-copy upload, and the **replacement HITL** at the end of an existing-case run (which is mandatory regardless of the tier). Convergence itself does not pause: Finalize and the holdout test run in the same turn.
- Do not read the target ContextSet file directly for mutations; always use `mutate_context_set`.
- A full run is long — 10 iterations × multi-minute evaluations each can take 30–60+ minutes. Warn the user upfront and run in a session that tolerates long work.
- When updating `state.md`, preserve the existing `## Metadata` and `## Active Database` sections (written by init) and append or update the `## Iteration Log` section and markers only.
- Never evaluate a working copy whose upload operation has not reported `done: true`.
- Only delete resources this run created: the single `<context_set_id>_draft` recorded in this run's `state.md` (existing case only), and only after the replacement decision has been made. **Never delete** a user-supplied seed resource or anything under the bare `<context_set_id>`. In the fresh case nothing is ever deleted.
- An existing context set under `<context_set_id>` is written **exactly once at most** — by the Finalize HITL, after the holdout test, with the user's explicit yes, and only if the candidate beats `v0`. Never during the loop, never on auto-approve, never on a resume without re-asking.
- Local `vN/context_set_vN.json` files are the only durable record of each iteration. Never rely on the Context Set server to recover a prior iteration.

## Tools

**Sibling skills:**
- `context-engineering-bootstrap` — cold-start path when no base context.
- `context-engineering-evaluate` — invoked once per iteration.
- `context-engineering-generation-guide` — produces well-formed Template / Facet / Value Search JSON.

**MCP — Context Set server (remote OneMCP server):**
- `upload_context_set` — overwrite the working copy every iteration (`<context_set_id>` in the fresh case, `<context_set_id>_draft` in the existing case); at the end, re-upload the best iteration to the working copy and, in the existing case after the user's yes, to the bare `<context_set_id>`; takes the file contents inline in `context_payload`; returns an operation.
- `delete_context_set` — existing case only: remove `<context_set_id>_draft` once, after the replacement decision; returns an operation.
- `get_operation` — poll every upload/delete operation (`project_id`, `location`, `operation_id`) until `done: true`.
- `get_context_set` — seed `v0` from a user-supplied existing resource (entry flow only). Location and existence checks are done by `context-engineering-init`, not here.

**MCP — local plugin and Toolbox:**
- `mutate_context_set` — apply planned mutations.
- `validate_context_set` — check `vN/context_set_vN.json` before every upload, and re-check after a user edits it during the manual-approval pause (`Auto-approve uploads: false`).
- `read_evaluation_result` — read scored eval reports.
- `<source>-list-schemas` (Toolbox) — validate that referenced columns exist.
- `<source>-execute-sql` (Toolbox) — validate that generated SQL runs against the DB.

**References:**
- `references/workspace.md` — internal workspace layout; load before any workspace interaction.
