# Hill-Climbing Workspace Layout

The hill-climbing skill maintains an internal workspace to track iterations, evaluation reports, and mutation history. Two roots matter:

- `<ce_root>` = `.context-engineering/` — **DB-level**, shared by every experiment on the same connection. Holds `tools.yaml`, the golden dataset and its splits.
- `<workspace_root>` = `.context-engineering/experiments/<experiment_name>/` — **per experiment**. Unless stated otherwise, paths below are relative to `<workspace_root>`.

## Directory structure

```
.context-engineering/                  # <ce_root> — one per DB connection
├── tools.yaml                         # DB connection (context-engineering-init)
├── golden.json                        # full golden dataset (context-engineering-dataset-generation)
├── splits/
│   ├── hillclimb.json                 # optimization split — the only dataset the loop evaluates on
│   └── holdout.json                   # held out until after publish; never read by the loop
└── experiments/
    └── <experiment_name>/             # <workspace_root> — one per experiment
        ├── state.md                   # written by init; metadata + per-iteration scores + notes
        ├── v<N>/                      # one directory per iteration (v0 = base, v1+ = mutations)
        │   ├── context_set_v<N>.json  # ContextSet snapshot at this version
        │   ├── analysis_v<N>.md       # findings + reasoning + mutations applied
        │   └── eval/                  # output_dir for context-engineering-evaluate
        │       ├── eval_configs/      # generated Evalbench YAMLs + converted dataset
        │       │   ├── db_config.yaml
        │       │   ├── model_config.yaml
        │       │   ├── run_config.yaml
        │       │   ├── llmrater_config.yaml
        │       │   └── golden_queries.json
        │       └── eval_reports/<job_id>/
        │           ├── scores.csv
        │           └── summary.csv
        ├── holdout_eval/              # output_dir for the single holdout evaluation (after publish)
        └── final_evaluation_report.md # generalizability report (Holdout Evaluation phase)
```

The dataset lives at `<ce_root>` because it describes the database, not an experiment: several experiments (different seeds, loop parameters, or context set ids) evaluate against the same `splits/hillclimb.json` / `splits/holdout.json`, which keeps their scores comparable.

## `state.md` format

`state.md` is the single source of truth for the experiment. `context-engineering-init` creates it with the first two sections; later phases append to it. It must contain:

- **Metadata** (init) — experiment name, workspace root, DB source (from `tools.yaml`), enrichment sources, the Context Store coordinates `project_id`, `location`, `context_set_id`, whether the user pre-approved draft uploads, and the loop parameters (tuning target, plateau k, max iterations). From the coordinates, the final resource is `projects/<project_id>/locations/<location>/contextSets/<context_set_id>` and the single working copy the loop overwrites every iteration is `projects/<project_id>/locations/<location>/contextSets/<context_set_id>_draft`. If `v0` is seeded from a user-supplied existing resource, record that resource name under `Seed resource` so it is never treated as a draft. Dataset paths point at `<ce_root>` (`.context-engineering/golden.json`, `.context-engineering/splits/…`) and are filled in by `context-engineering-dataset-generation`; if the splits already exist when init runs (a second experiment on the same DB), init records them directly.
- **Active Database** (init; expanded by dataset-generation) — source name, type, and `**Graph Ids**` for Spanner GoogleSQL property graphs. `generate_evalbench_configs` reads `**Graph Ids**` from this file to scope Spanner Graph evaluations.
- **Iteration Log** (hillclimb) — one entry per completed iteration, in order. Each entry records the local file, the score, and the working-copy upload operation name.
- **Final** (hillclimb) — written once by the Finalize step, together with the working-copy delete operation.
- **Generalizability** (holdout phase) — holdout score, verdict, and triage path.

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
- Draft resource: projects/my-project/locations/us-central1/contextSets/my-exp-1_draft
- Seed resource: (none — seeded from bootstrap)
- Overwrite acknowledged: yes (bare id did not exist at init)
- Auto-approve uploads: true
- Tuning target: 1.0
- Plateau k: 3
- Max iterations: 10
- Golden dataset: ./.context-engineering/golden.json
- Hillclimb dataset: ./.context-engineering/splits/hillclimb.json
- Holdout dataset: ./.context-engineering/splits/holdout.json

## Active Database
- **Source Name**: my-alloydb
- **Type**: alloydb-postgres
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

## Final: projects/my-project/locations/us-central1/contextSets/my-exp-1 (from v1, score 0.58)
- Upload op: projects/my-project/locations/us-central1/operations/operation-...-f1
- Draft delete op: projects/my-project/locations/us-central1/operations/operation-...-f2

## Generalizability
- Holdout eval: holdout_eval/eval_reports/<job_id>/
- Holdout score: 0.56 (25 / 45)
- Verdict: PASS
- Report: final_evaluation_report.md
```

## Markers

`state.md` carries markers that let a resume distinguish crash, intentional stop, user stop, and an interrupted finalize:

- `## In-Progress: vN` — written at the start of iteration N (per-iteration step 1); removed when the final `### vN` entry lands (per-iteration step 6). A leftover marker means the prior run crashed mid-iteration — possibly after `<context_set_id>_draft` was already overwritten with the half-finished `vN`.
- `## Converged: <reason>` — appended when a stopping condition fires. `<reason>` is one of `Tuning target <t> reached.`, `Plateau reached over <k> consecutive mutations.`, `Maximum iterations (<n>) reached.`
- `## User Stop` — appended when the user explicitly asks the loop to stop.
- `## Finalizing: vK` — written immediately before the final upload; replaced by `## Final: …` when that upload's operation **and** the `_draft` delete operation both report `done: true`. A leftover marker means finalize was interrupted.
- `## Generalizability` — written by the Holdout Evaluation phase after `## Final:`. Its presence means the run is complete.

Completed iterations are preserved in every case — never deleted.

## Resume rules

On skill invocation with an existing workspace:

1. Read `state.md`.
2. Determine the last completed iteration `N`.
3. **Check for `## In-Progress: vM`.** If present, the prior run crashed mid-iteration `M`:
   1. Delete the partial `vM/` directory (its files are not trustworthy) and remove the marker.
   2. The working copy may hold a half-finished `vM`: re-upload `v(M-1)/context_set_v(M-1).json` to `<context_set_id>_draft` (read the file, pass its exact contents as `context_payload`) and poll `get_operation` until `done: true`.
   3. Re-run iteration `M` from step 1, seeding from `v(M-1)/context_set_v(M-1).json` on disk.
4. **Check for `## Finalizing: vK`.** If present, finalize was interrupted. Re-run the Finalize step from `vK/context_set_vK.json` (the re-upload is unconditional — do not try to infer what the server holds) and poll until `done: true`. No re-confirmation is needed: the overwrite was acknowledged in init.
5. If `## Final:` is present without `## Generalizability`, run the **Holdout Evaluation & Generalization Reporting Phase** (workflow skill) against the final resource, then stop.
6. If `## Final:` and `## Generalizability` are both present, report them and stop — the run is complete.
7. If `## Converged` is present without `## Final:`, run the Finalize step — do not start another iteration.
8. If `## User Stop` is present without `## Final:`, ask once whether to continue the loop or publish `best_version` now; do not publish on your own.
9. Otherwise, start iteration `N+1`. Check the stopping conditions first — a resume after `Max iterations` must not add an iteration.

## Final output

The resource recorded under `## Final:` — the bare `<context_set_id>` uploaded from the best-scoring iteration's local file — together with that score, local file path, and the generalizability verdict. No `_draft` resource should remain in the store when a run is complete.
