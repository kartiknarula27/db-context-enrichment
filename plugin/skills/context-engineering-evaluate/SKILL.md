---
name: context-engineering-evaluate
description: Guides the agent to execute an evaluation of a ContextSet against a golden NLQ+SQL dataset using the Evalbench framework.
---

> **Load [`context-engineering-workflow`](../context-engineering-workflow/SKILL.md) first** for shared terminology, lifecycle overview, and safety protocol.

# Skill: Evaluation Scoring

## Goal
Score a ContextSet against a golden dataset by running Evalbench, and return a scored report — overall accuracy plus dominant failure categories.

## Prerequisites

- A working DB connection (`tools.yaml` configured for the Toolbox MCP server — see `context-engineering-init` if missing).
- ADC configured and the Gemini Data Analytics + Dataplex APIs enabled on the project (see `context-engineering-init` for preflight).
- A golden evaluation dataset (absolute path) in the **simplified user-facing format** — a JSON list of objects, each with:
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
  - **`cs_resource_name`** — a full Context Store resource name (e.g., `projects/<project_id>/locations/<location>/contextSets/<context_set_id>`). Used directly.
  - **Local ContextSet JSON file** + `project_id`, `location`, and a `context_set_id` — the skill uploads it (with explicit user consent) to obtain a `cs_resource_name` for the run. See the Context Store (OneMCP) Protocol in `context-engineering-workflow`.

- An `output_dir` (absolute or workspace-relative) where the eval configs and reports should live. If the user hasn't specified one, prompt them; a sensible suggestion is `./eval-runs/<name>/`.

## Guidance

1. **Collect inputs.** Prompt only for what's missing from the Prerequisites. Trust `tools.yaml` values as-is — don't ask the user to re-verify them.
   - **Dataset Selection & Dev/Test Split Check**:
     - Check for available dataset files in the workspace (or `<output_dir>/splits/`): `splits/dev.json`, `splits/test.json`, or the full golden dataset.
     - **If `splits/dev.json` exists**: Default to evaluating on `splits/dev.json` (or prompt the user if they specifically want to score `test.json` or the full dataset).
     - **If `splits/` does NOT exist yet**: Ask the user:
       > *"Would you like to set up a Dev/Test split now (via the `split_dataset` tool to prepare for generalizability testing during hill-climbing), or run a baseline evaluation against all questions?"*
       - If they choose Dev/Test split: Invoke the `split_dataset` MCP tool with `golden_dataset_path` and `output_dir` to generate `splits/dev.json` and `splits/test.json`, then evaluate on `splits/dev.json`.
       - If they choose full evaluation: Evaluate directly against the provided golden dataset.

2. **Prepare the ContextSet resource name.**
   - If the user supplied a `cs_resource_name`, use it directly.
   - If the user supplied a local file:
     - Confirm `project_id`, `location`, and `context_set_id` are all present. Ask for any missing value individually — do not guess.
     - Call `list_context_set_locations(project_id)` and confirm `location` is in the returned list; if not, let the user pick one from it.
     - Build `context_set = projects/<project_id>/locations/<location>/contextSets/<context_set_id>` and ask for explicit consent before uploading. Show the exact resource name, and warn that if a context set with this name already exists the upload **overwrites it** irrecoverably.
     - On consent, call `upload_context_set(context_set=<context_set>, local_file_path=<file>)`.
     - Poll `get_operation` on the returned operation (1 s doubling backoff, 5-minute ceiling) until `done: true` before continuing — see the Context Store (OneMCP) Protocol. **Do not proceed to step 3 or call `generate_evalbench_configs` until `done: true` is observed**; evaluating before the upload has landed scores a missing or stale context set.
     - On `done: true`, `<context_set>` becomes `cs_resource_name` for the rest of this run.
     - On an `upload_context_set` failure or an operation that reports an `error`, surface it verbatim and stop. Do not fall back to a manual upload URL.

3. **Select the DB source from `tools.yaml`.**
   - Find all `kind: source` blocks whose `type` is a supported evaluation engine (consult `generate_evalbench_configs` for the current list).
   - If exactly one supported source exists, inform the user and auto-select it.
   - If multiple, list their `name` + `type` and let the user pick.

4. **Generate the Evalbench configs.** Call `generate_evalbench_configs`. The tool writes configs under `<output_dir>/eval_configs/`. This is the only supported way to produce Evalbench configs — never author them by hand.

5. **Run Evalbench.** 
   - **Environment Variables**: Ensure the required GCP environment variables are exported:
     ```bash
     export EVAL_GCP_PROJECT_ID="<project_id>"
     export EVAL_GCP_PROJECT_REGION="global"
     ```
  - Shell out from the caller's cwd: `uvx google-evalbench@1.17.0 --experiment_config=<output_dir>/eval_configs/run_config.yaml`
  - Reports materialize under `<output_dir>/eval_reports/<job_id>/` (the job_id appears in the tool's stdout). The run can take many minutes for larger datasets — let it complete; do not kill or restart on apparent stalls. Treat a non-zero exit code as a hard failure and surface stderr verbatim.

6. **Read and summarize results.** Call `read_evaluation_result` on the run folder `<output_dir>/eval_reports/<job_id>/`. Report to the user: overall score, dominant failure categories, and the absolute path to the full reports. Suggest hillclimb as a natural next step if the user wants to iteratively improve.

7. **Handling QueryData API Errors**: If the evaluation results or failure cases indicate a API error under `SQL Generator Error`:
  1. Distinguish this from a context/prompt quality defect.
  2. Inform the user that the evaluation encountered an API error, and advise them on how to fix. We must fix these errors before we have usable evaluation results.
  3. For errors related to API field access, advice the caller to verify API field visibility: "Your request references pre-release or private preview feature. Please ensure your GCP project is allowlisted for access by contacting your Google Cloud account team."


## Rules

- Never upload a ContextSet without explicit user consent.
- Never invoke bootstrap or hillclimb from within this skill.
- If both `cs_resource_name` and a local file are provided, ask the user which to use — do not silently pick.
- On `generate_evalbench_configs` errors, surface the error and stop; do not retry blindly.
- This skill is stateless. Every path comes from the caller — do not assume a workspace layout or write cross-phase state files.
- Use the caller's Context Store coordinates (`project_id`, `location`, `context_set_id`) verbatim when supplied. If any are missing, ask the user explicitly — do not infer from filenames, paths, or `tools.yaml` without their confirmation.
- Never evaluate a context set whose upload operation has not reported `done: true`.

## Tools

**MCP:**
- `list_context_set_locations` — confirm the upload location; used only when the caller supplies a local file.
- `upload_context_set` — used only when the caller supplies a local file instead of a resource name; returns an operation.
- `get_operation` — poll the upload operation until `done: true` before evaluating.
- `generate_evalbench_configs` — produces Evalbench YAML configs on disk.
- `read_evaluation_result` — parses `scores.csv` / `summary.csv` into a markdown summary.

**Shell:**
- `uvx google-evalbench@1.10.0 --experiment_config=<path>` — runs the eval job against the published release.

**References:**
- `references/<db_type>.md` (`alloydb-postgres.md`, `cloud-sql-mysql.md`, `cloud-sql-postgres.md`, `spanner.md`) — per-engine schema examples for fixing `tools.yaml` source blocks if `generate_evalbench_configs` reports a validation failure.
