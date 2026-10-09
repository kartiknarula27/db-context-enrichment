---
name: context-engineering-evaluate
description: Guides the agent to execute an evaluation of a ContextSet against a golden NLQ+SQL dataset using the Evalbench framework, either standalone or as one iteration of the hill-climbing loop.
---

> **Load [`context-engineering-workflow`](../context-engineering-workflow/SKILL.md) first** for shared terminology, the Context Store (OneMCP) Protocol, and safety protocol.

# Skill: Evaluation Scoring

## Goal
Score a ContextSet against a dataset by running Evalbench, and return a scored result — overall accuracy, raw `passed` / `total` counts, the report directory, plus dominant failure categories.

> [!IMPORTANT]
> **Evaluate is a pure function over its inputs.** It reads whatever the caller hands it, writes only under the caller's `output_dir`, and **never modifies `state.json`**. The caller (hillclimb, the holdout step, or the user) records the returned numbers. This keeps one writer per field and lets evaluate be used on any file or resource without side effects on an experiment.


## Prerequisites

### Inside an experiment (called by hillclimb or the holdout step, or by the user on an existing experiment)

Read `.context-engineering/experiments/<experiment_name>/state.json` and take:

| Input | From |
| :--- | :--- |
| Toolbox source | `db_source` (and `.context-engineering/tools.yaml` must contain it). |
| Dataset | `dataset_paths.hillclimb` by default; `dataset_paths.holdout` **only** when the caller is the holdout step; `dataset_paths.golden` only if the user explicitly asks for a full-set score. Never offer the holdout split as a casual choice — it must stay unseen during optimization. |
| ContextSet resource | The caller names it: the `_draft` working copy during the loop, the bare `<context_set_id>` for the holdout step, or a resource the user names. |
| `output_dir` | Supplied by the caller: `hillclimb/vN/eval/` during the loop, `hillclimb/v<best>/holdout_eval/` for the holdout step. |
| GCP project | `context_store_coordinates.project_id`. |

If there is no `state.json` for the experiment the user is talking about, route to [context-engineering-init](../context-engineering-init/SKILL.md) rather than collecting the values inline.

### Standalone (no experiment)

Ask only for what is missing:

- A working DB connection (`tools.yaml` configured for the Toolbox MCP server — see `context-engineering-init` if missing).
- ADC configured and the Gemini Data Analytics + Dataplex APIs enabled on the project (see `context-engineering-init` for preflight).
- A dataset (absolute path) in the **simplified user-facing format** — a JSON list of objects, each with:
  - `id`: unique identifier (e.g., `eval_001`).
  - `database`: target database name.
  - `nlq`: natural language question.
  - `golden_sql`: correct reference SQL query.

  Example:
  ```json
  [
    {
      "id": "eval_001",
      "database": "my_db",
      "nlq": "Count users",
      "golden_sql": "SELECT COUNT(*) FROM users"
    }
  ]
  ```
- A ContextSet, supplied as **exactly one of**:
  - **A full Context Store resource name** (`projects/<project_id>/locations/<location>/contextSets/<context_set_id>`). Used directly.
  - **A local ContextSet JSON file.** QueryData can only use a context set that is in the Context Store, so the file must be uploaded first — see step 2.
- An `output_dir` (absolute or workspace-relative) where the eval configs and reports should live. If the user hasn't specified one, suggest `./eval-runs/<name>/`.

## Guidance

1. **Collect inputs** per the applicable Prerequisites table. Trust `tools.yaml` values as-is — don't ask the user to re-verify them.

2. **Prepare the ContextSet resource name.**
   - A resource name is used directly.
   - For a **local file**:
     - Say plainly: *"This local file must be pushed to GCP to run evaluation. May I upload it?"* and name the target.
     - **Inside an experiment** the target is the working copy `projects/<project_id>/locations/<location>/contextSets/<context_set_id>_draft` from `context_store_coordinates`; if `auto_approve_uploads` is `true` the question above is informational and you proceed, otherwise wait for approval. Never upload to the bare id from evaluate.
     - **Standalone** collect `project_id`, `location` and a `context_set_id` individually (do not guess); call `list_context_set_locations(project_id)` and make sure `location` is in the list; show the exact resource name; warn that if a context set with this name already exists the upload **overwrites it** irrecoverably; proceed only on explicit consent.
     - Run `validate_context_set` on the file first; then read the file and call `upload_context_set(project_id=<project_id>, location=<location>, context_set_id=<context_set_id>, context_payload=<exact file contents>)` — the three identity parts come from the resource name above (`context_set_id` is its last path segment, `<context_set_id>_draft` inside an experiment). There is no file-path argument.
     - Poll `get_operation(project_id, location, operation_id)` on the returned operation (≥5 s between polls, 5-minute ceiling), logging each response, until `done: true` — see the Context Store (OneMCP) Protocol in `context-engineering-workflow`. **Do not call `generate_evalbench_configs` until `done: true` is observed**; evaluating before the upload has landed scores a missing or stale context set.
     - On an operation that reports an `error`, surface it verbatim and stop.

3. **Select the DB source.** Inside an experiment it is `db_source`. Standalone: find all `kind: source` blocks in `tools.yaml` whose `type` is a supported evaluation engine (consult `generate_evalbench_configs` for the current list); auto-select if exactly one, otherwise list `name` + `type` and let the user pick.

4. **Generate the Evalbench configs.** Call `generate_evalbench_configs(output_dir, dataset_path, context_set_id=<resource>, toolbox_config_path=".context-engineering/tools.yaml", toolbox_source_name=<db_source>)`. The tool writes configs under `<output_dir>/eval_configs/`. This is the only supported way to produce Evalbench configs — never author them by hand.

5. **Run Evalbench.**
   - **Environment variables**: ensure the required GCP environment variables are exported:
     ```bash
     export EVAL_GCP_PROJECT_ID="<project_id>"
     export EVAL_GCP_PROJECT_REGION="global"
     ```
   - Shell out from the caller's cwd: `uvx google-evalbench@1.17.0 --experiment_config=<output_dir>/eval_configs/run_config.yaml`
   - Reports materialize under `<output_dir>/eval_reports/<job_id>/` (the `job_id` appears in the tool's stdout). The run can take many minutes for larger datasets — let it complete; do not kill or restart on apparent stalls. Treat a non-zero exit code as a hard failure and surface stderr verbatim.

6. **Read and return results.** Call `read_evaluation_result` on `<output_dir>/eval_reports/<job_id>/`. Return to the caller:
   ```
   {passed, total, accuracy, job_id, eval_dir: "<output_dir>/eval_reports/<job_id>/"}
   ```
   plus the dominant failure categories and the absolute path to the full reports.
   - **Standalone**: present these in chat and suggest hillclimb as the natural next step if the user wants to iteratively improve.
   - **Inside the loop**: hand the numbers back to hillclimb; it decides what to say and writes `state.json`. The workflow's *Master Loop Control* supersedes any "next steps" summary here.

7. **Handling QueryData API errors**: if the evaluation results or failure cases show an API error under `SQL Generator Error`:
   1. Distinguish this from a context/prompt quality defect.
   2. Inform the user that the evaluation encountered an API error and advise them on how to fix it. These errors must be fixed before the results are usable; **do not record the score** of such a run as an iteration result.
   3. For errors related to API field access, advise the caller to verify API field visibility: "Your request references pre-release or private preview feature. Please ensure your GCP project is allowlisted for access by contacting your Google Cloud account team."

## Rules

- Never write `state.json`. Return results; the caller records them.
- Never evaluate a context set whose upload operation has not reported `done: true`.
- Never upload to the bare `<context_set_id>` of an experiment; evaluate's only upload target inside an experiment is `_draft`.
- Never pick the holdout split on your own initiative; it is used once, by the holdout step, after publish.
- Never invoke bootstrap or hillclimb from within this skill.
- If both a resource name and a local file are provided, ask the user which to use — do not silently pick.
- On `generate_evalbench_configs` errors, surface the error and stop; do not retry blindly.
- Standalone, use the caller's Context Store coordinates verbatim; if any are missing, ask explicitly — do not infer from filenames, paths, or `tools.yaml` without confirmation.

## Tools

**MCP:**
- `validate_context_set` — check a local file before uploading it.
- `list_context_set_locations` (remote Context Store server) — confirm the upload location; standalone local-file path only.
- `upload_context_set` (remote Context Store server) — used only when the caller supplies a local file instead of a resource name; takes the file contents inline in `context_payload`; returns an operation.
- `get_operation` (remote Context Store server) — poll the upload operation until `done: true` before evaluating.
- `generate_evalbench_configs` — produces Evalbench YAML configs on disk.
- `read_evaluation_result` — parses `scores.csv` / `summary.csv` into a markdown summary.

**Shell:**
- `uvx google-evalbench@1.17.0 --experiment_config=<path>` — runs the eval job against the published release.

**References:**
- `references/<db_type>.md` (`alloydb-postgres.md`, `cloud-sql-mysql.md`, `cloud-sql-postgres.md`, `spanner.md`) — per-engine schema examples for fixing `tools.yaml` source blocks if `generate_evalbench_configs` reports a validation failure.
