# Paper figures

From the repository root, run:

```sh
python3 paper/refresh.py
```

Requires Python with Matplotlib (`python3 -m pip install -r paper/requirements.txt`) and Graphviz's `dot` command. The system Python currently has Matplotlib; the application's `venv` does not.

Open `paper/generated/index.html` for previews and SVG, PDF, and 220-DPI PNG downloads. SVG/PDF preserve vector geometry for publication. Graphviz sources are also retained. No database, network, model calls, or live financial data are used.

## Updating the figures

Edit `paper/figures.json` to select completed evaluation folders, a budget accuracy result, and a guidance concept key. Then rerun the command. Workflow figures always read the current chat, budget, and close graph definitions; the guidance figure reads `kb/graph.json`. This is an on-demand refresh, not a background watcher.

The evaluation selection is the final benchmark from November 5 (commit `635b529`): the three model baselines, each with every scenario run 3 times. `benchmark` names that date's folder, and `benchmark_figures.py` draws three more charts from it: models, chat retrieval modes and budget improvement. Those count a scenario as passing on 2 of 3 runs, as the preregistration does. The per-model snapshot panels show run-level rates instead. The verdict tables are in `eval/results/2026-11-05/tables.md`, made by `eval/make_tables.py`. Selection is explicit so a newer diagnostic run cannot silently replace paper evidence. Each panel uses its own reported denominator; absent metrics are omitted, not replaced with zero. For a final comparison use compatible commits, scenario sets/hashes, and settings, and follow `eval/preregistration.md` for majority-of-three counts and verdicts. No significance claims are generated.

The budget plot compares mean versus median initial estimates, not agent improvement over time. Captions explain the holdout and zero-actual exclusions. The graph neighborhood includes all stored versions of incident edges; dates are validity intervals and do not imply facts are current.

`manifest.json` records selected paths, SHA-256 source hashes, and captions. Keep it with exports for reproducibility. Figures refresh from source files, but editorial captions should be reviewed if evaluation methodology changes. Goal routing uses implicit lambda destinations and is not included in the automatic route-map extractor. The extractor fails on missing explicit route maps instead of inventing connections.

## Temporal knowledge graph

The first two gallery figures now distinguish the domain graph from agent control flow:

- **Temporal knowledge graph:** every allowed relation and endpoint type, extracted from the graph store's enforced schema. Personal entities and shared guidance have different colors. This is a schema view, not a populated database export.
- **Temporal edge history:** Alice's gym subscription from the existing independent query-test fixture. Both valid-time and recorded-time intervals are shown for all five retained edge versions, including the $45-to-$40 correction and the later-recorded closure. This is explicitly synthetic test data, not measured user activity.

Both regenerate with the same command. Their source files are included in the provenance manifest. The temporal fixture reader accepts literal expressions only and does not import application code or execute tests. Live database snapshots are not exported by this command.

## Concrete examples for presentations

Three additional example graphs explain how the relationships are used:

1. Subscription price history: two dated versions identify a $5 increase.
2. Budget context: connect a limit, monthly spending, recurring charge, and earlier advice outcome. Context supports diagnosis; it does not by itself prove causation.
3. Guidance retrieval: connect a personal account to a shared concept, stored rule, and source.

Personal entities and amounts are explicitly synthetic. The guidance example reads its shared relationships from `kb/graph.json`. All example edge types are validated against the store's allowed endpoint schema on every refresh. The examples explain supported data relationships; they are not captured agent execution traces or measured outcomes.
