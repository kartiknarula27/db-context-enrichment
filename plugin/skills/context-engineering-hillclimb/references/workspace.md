# Hill-Climbing Workspace Layout

The hill-climbing skill maintains an internal workspace to track iterations, evaluation reports, and mutation history. Everything for one run lives under a single root:

- `.context-engineering/tools.yaml` — the **single shared** Toolbox configuration for the workspace (user-owned; may hold several DB sources). The Toolbox MCP server reads it at startup only, so any edit — by a skill or by the user — needs a `toolbox` server restart.
- `<workspace_root>` = `.context-engineering/experiments/<experiment_name>/` — **per experiment**: `state.md`, `dataset/`, iterations and reports. `state.md`'s `## Active Database` records which `tools.yaml` source (and tools) this experiment uses. Unless stated otherwise, paths below are relative to `<workspace_root>`.

## Directory structure

```
.context-engineering/
├── tools.yaml                             # shared Toolbox DB connections (context-engineering-init / user)
└── experiments/
    └── <experiment_name>/                 # <workspace_root> — one per experiment
        ├── state.md                       # written by init; metadata + which tools.yaml source is used + per-iteration scores
        ├── dataset/                       # context-engineering-dataset-generation (deliverables + working files)
        │   ├── golden.json                # full golden dataset
        │   ├── splits/
        │   │   ├── hillclimb.json         # optimization split — the only dataset the loop evaluates on
        │   │   └── holdout.json           # held out until the loop converges; never read by the loop
        │   ├── evalset_environment_inputs.md  # domain map, artifact registry, seed pairs
        │   ├── evalset_gen_plan.md        # user-approved generation plan
        │   ├── temp_golden.json           # interim dataset during generation/expansion
        │   ├── evalset_report_pair_level.md     # audit report 1
        │   └── evalset_report_dataset_level.md  # audit report 2
        ├── v<N>/                          # one directory per iteration (v0 = base, v1+ = mutations)
        │   ├── context_set_v<N>.json      # ContextSet snapshot at this version
        │   ├── analysis_v<N>.md           # findings + reasoning + mutations applied
        │   └── eval/                      # output_dir for context-engineering-evaluate
        │       ├── eval_configs/          # generated Evalbench YAMLs + converted dataset
        │       │   ├── db_config.yaml
        │       │   ├── model_config.yaml
        │       │   ├── run_config.yaml
        │       │   ├── llmrater_config.yaml
        │       │   └── golden_queries.json
        │       └── eval_reports/<job_id>/
        │           ├── scores.csv
        │           └── summary.csv
        ├── holdout_eval/                  # output_dir for the single holdout evaluation (after convergence, before any replacement)
        └── final_evaluation_report.md     # generalizability report (Holdout Evaluation phase)
```

An experiment is **self-contained and, once `## Final:` is written, frozen**. Follow-up work happens in a new experiment that `context-engineering-init` **clones** from the finished one: `dataset/` in full when the fix is to the context (identical splits → comparable verdicts), or only `dataset/golden.json` as a seed when the fix is to the dataset (which is then expanded and re-split). The clone points at the **same `tools.yaml` source** (carried over in `## Active Database`; the shared `tools.yaml` is never copied). The finished experiment's result (its published context set, or its best local file if it was not published) becomes the clone's `Seed resource` and is never overwritten.

## `state.md` format

`state.md` is the single source of truth for the experiment. `context-engineering-init` creates it with the first two sections; later phases append to it. It must contain:

- **Metadata** (init) — experiment name, workspace root, DB source (one of the sources in the shared `.context-engineering/tools.yaml`), enrichment sources, the Context Set coordinates `project_id`, `location`, `context_set_id`, and whether the user pre-approved iteration uploads (default `false`). Stopping rules (tuning target, plateau, iteration cap) are internal constants of the hillclimb skill and are **not** recorded here — only the outcome (`## Converged: <reason>`) is. From the coordinates, init derives `Final resource` using the **Resource naming rule** in `context-engineering-workflow` (the examples below show the current shape, but that rule is the only definition of it) and probes whether it already exists; the result is the **`Draft resource`** bullet, which decides the working copy: `(none — new context set; iterations upload to the final name)` for a fresh name, or `…/<context_set_id>_draft` when the name already existed (the existing set is then never written during the run and only replaced after the holdout test and an explicit yes). If `v0` is seeded from a user-supplied existing resource, record that resource name **verbatim** under `Seed resource` (whatever collection segment it uses). For a cloned experiment, record `Cloned from: <source experiment> (context fixes | dataset expansion)`. Dataset paths point inside the experiment (`dataset/golden.json`, `dataset/splits/…`) and are filled in by `context-engineering-dataset-generation`; when init cloned `dataset/` in full, init records them directly.
- **Active Database** (init; expanded by dataset-generation) — **which `tools.yaml` source this experiment uses**: `**Config**` (always `./.context-engineering/tools.yaml`), `**Source Name**`, `**Type**`, `**Tools**` (the exact tool names declared for that source), and `**Graph Ids**` for Spanner GoogleSQL property graphs. Downstream skills take the source from here rather than guessing from a multi-source `tools.yaml`; `generate_evalbench_configs` reads `**Graph Ids**` from this file to scope Spanner Graph evaluations.
- **Iteration Log** (hillclimb) — one entry per completed iteration, in order. Each entry records the local file, the score, and the working-copy upload operation name.
- **Generalizability** (holdout phase, written **before** `## Final`) — the candidate evaluated (the working copy), holdout score, verdict, and report path.
- **Final** (hillclimb) — written last by the Finalize step: where the result lives (or "not published"), plus the publish / draft-delete operation names where applicable. Its presence marks the experiment as finished and read-only.

Example:

```markdown
# Hill-Climbing Experiment: my-exp-1

## Metadata
- Workspace: ./.context-engineering/experiments/my-exp-1/
- DB source: my-alloydb
- Enrichment sources: ./docs/design.md, ./app/models/
- Project: my-project
- Location: us-central1
- Context set id: my-exp-1
- Final resource: projects/my-project/locations/us-central1/contextSets/my-exp-1
- Draft resource: (none — new context set; iterations upload to the final name)
- Seed resource: (none — seeded from bootstrap)
- Cloned from: (none)
- Auto-approve uploads: false
- Golden dataset: ./.context-engineering/experiments/my-exp-1/dataset/golden.json
- Hillclimb dataset: ./.context-engineering/experiments/my-exp-1/dataset/splits/hillclimb.json
- Holdout dataset: ./.context-engineering/experiments/my-exp-1/dataset/splits/holdout.json

## Active Database
- **Config**: ./.context-engineering/tools.yaml
- **Source Name**: my-alloydb
- **Type**: alloydb-postgres
- **Tools**: my-alloydb-list-schemas, my-alloydb-execute-sql
- **Graph Ids**: []

## Iteration Log

### v0 (base)
- Local file: v0/context_set_v0.json
- Upload op: projects/my-project/locations/us-central1/operations/operation-...-a1
- Score: 0.42
- Eval report: v0/eval/eval_reports/<job_id>/
- Notes: baseline generated by context-engineering-bootstrap

### v1
- Local file: v1/context_set_v1.json
- Upload op: projects/my-project/locations/us-central1/operations/operation-...-b1
- Score: 0.58
- Analysis: v1/analysis_v1.md
- Eval report: v1/eval/eval_reports/<job_id>/

### v2
- Local file: v2/context_set_v2.json
- Upload op: projects/my-project/locations/us-central1/operations/operation-...-c1
- Score: 0.55
- Analysis: v2/analysis_v2.md
- Eval report: v2/eval/eval_reports/<job_id>/

## Converged: Plateau reached over 3 consecutive mutations.

## Generalizability
- Candidate: projects/my-project/locations/us-central1/contextSets/my-exp-1 (v1 in the working copy)
- Holdout eval: holdout_eval/eval_reports/<job_id>/
- Holdout score: 0.56 (25 / 45)
- Verdict: PASS
- Report: final_evaluation_report.md

## Final: projects/my-project/locations/us-central1/contextSets/my-exp-1 (from v1, score 0.58)
- Candidate upload op: projects/my-project/locations/us-central1/operations/operation-...-f1
```

The `## Final:` line takes one of three forms — fresh case: `## Final: <Final resource> (from vK, score S)`; existing case, replaced: `## Final: <Final resource> (from vK, score S) — replaced previous contents` with the publish and draft-delete operation names; existing case, kept: `## Final: not published — <Final resource> unchanged; best candidate vK/context_set_vK.json (score S)` (or `… — no improvement over v0; …`) with the draft-delete operation name.

## Markers

`state.md` carries markers that let a resume distinguish crash, intentional stop, user stop, and an interrupted finalize. "Working copy" below means the bare `<context_set_id>` when `Draft resource` is `(none …)` (fresh case) and `<context_set_id>_draft` otherwise (existing case).

- `## In-Progress: vN` — written at the start of iteration N (per-iteration step 1); removed when the final `### vN` entry lands (per-iteration step 6). A leftover marker means the prior run crashed mid-iteration — possibly after the working copy was already overwritten with the half-finished `vN`.
- `## Converged: <reason>` — appended when a stopping condition fires. `<reason>` is one of `Tuning target <t> reached.`, `Plateau reached over <k> consecutive mutations.`, `Maximum iterations (<n>) reached.`
- `## User Stop` — appended when the user explicitly asks the loop to stop.
- `## Finalizing: vK` — written when the candidate `vK` is chosen, before it is (re-)uploaded to the working copy. Replaced by `## Final: …` at the very end of Finalize. A leftover marker means finalize was interrupted somewhere between candidate upload, holdout test and publication decision.
- `## Generalizability` — written by the Holdout Evaluation phase **before** `## Final:`; it records the verdict for the candidate in the working copy.
- `## Final: …` — written last. **Its presence means the run is complete** and the experiment is read-only.

Completed iterations are preserved in every case — never deleted.

## Resume rules

On skill invocation with an existing workspace:

1. Read `state.md`.
2. Determine the last completed iteration `N`.
3. **Check for `## In-Progress: vM`.** If present, the prior run crashed mid-iteration `M`:
   1. Delete the partial `vM/` directory (its files are not trustworthy) and remove the marker.
   2. The working copy may hold a half-finished `vM`: re-upload `v(M-1)/context_set_v(M-1).json` to the working copy (read the file, pass its exact contents as `context_payload`) and poll `get_operation` until `done: true`.
   3. Re-run iteration `M` from step 1, seeding from `v(M-1)/context_set_v(M-1).json` on disk.
4. **Check for `## Finalizing: vK`** (and no `## Final:`). Finalize was interrupted. Re-upload `vK/context_set_vK.json` to the working copy unconditionally (do not try to infer what the server holds), then: if `## Generalizability` is missing, run the Holdout Evaluation phase; then run Finalize step 4 — in the existing case this means **asking the replacement question again** (never assume an earlier answer), then deleting the draft (NOT_FOUND = success) — and write `## Final:`.
5. If `## Final:` is present, the experiment is **finished and read-only**: do not add iterations, uploads, or dataset changes. Report the final line and verdict; for non-PASS verdicts re-print the *Next experiment hand-off* card from `context-engineering-workflow` (clone scope per the verdict), then stop. If the user wants to improve further, route to `context-engineering-init` in clone mode.
6. If `## Converged` is present without `## Finalizing`, run Finalize from step 1 — do not start another iteration.
7. If `## User Stop` is present without `## Final:`, ask once whether to continue the loop or finalize now; do not finalize on your own.
8. Otherwise, start iteration `N+1`. Check the stopping conditions first — a resume after the iteration cap must not add an iteration.

## Final output

The `## Final:` line — either the resource the best-scoring iteration now lives under, or "not published" with the existing resource left unchanged — together with the candidate's score, local file path, and the generalizability verdict. No `_draft` resource should remain on the server when a run is complete.
