"""Final benchmark charts (TPC-28): models, chat retrieval modes, and budget improvement over periods."""
import json
import re
import sys

import matplotlib.pyplot as plt

MODELS = ('qwen2.5_14b', 'llama3.1_8b', 'mistral')
MODES = (('None', 'chat-none'), ('Vector', 'chat-vector'), ('Graph', 'chat-graph'),
         ('Hybrid', 'baseline'), ('Hybrid,\nshuffled', 'chat-hybrid-shuffled'))
GROUPS = (('temporal', 'Temporal'), ('graph_suited', 'Graph-suited'), ('graph_unsuited', 'Graph-unsuited'))
COLORS = ('#21618c', '#148f77', '#af601a', '#7d3c98', '#717d7e')


def generate(root, track, plot, folder):
    sys.path.insert(0, str(root))
    from eval.compare import agent_passed, budget_passed, chat_passed, load_runs, scenario_passes
    base = root / folder
    track(root / 'eval/compare.py')

    def finished(name):
        found = []
        for path in base.iterdir():
            m = re.fullmatch(re.escape(name) + r'(?:-(\d+))?', path.name)
            if m and (path / 'summary.json').is_file() and json.loads((path / 'config.json').read_text()).get('finished_at'):
                found.append((int(m.group(1) or 1), path))
        if not found:
            raise ValueError(f'No finished run for {name} in {folder}')
        path = max(found)[1]
        for f in ('config.json', 'summary.json'):
            track(path / f)
        return path

    def passes(path, kind, passed, tag=None):
        _, runs = load_runs(path)
        items = [v for v in runs.values() if v[0]['record'].get('kind', 'chat') == kind]
        if tag:
            tags = {}
            for p in (root / 'eval/scenarios').glob('*.json'):
                d = json.loads(p.read_text())
                tags[d['id']] = d.get('tags', ())
            items = [v for v in items if tag in tags.get(v[0]['record']['scenario_id'], ())]
        return sum(1 for v in items if scenario_passes(v, passed)), len(items)

    # models
    kinds = (('Chat facts', 'chat', chat_passed), ('Budget cause and action', 'budget', budget_passed), ('Goal checks', 'goal', agent_passed))
    fig, ax = plt.subplots(figsize=(8, 3.8))
    width = 0.26
    for i, model in enumerate(MODELS):
        path = finished(f'{model}-baseline')
        counts = [passes(path, k, f) for _, k, f in kinds]
        bars = ax.bar([x + (i - 1) * width for x in range(len(kinds))], [100 * a / n for a, n in counts], width,
                      label=model.replace('_', ':'), color=COLORS[i])
        ax.bar_label(bars, labels=[f'{a}/{n}' for a, n in counts], fontsize=8, padding=2)
    ax.set_xticks(range(len(kinds)), [k[0] for k in kinds])
    ax.set(ylim=(0, 115), ylabel='Scenarios passing (%)', title='Agent models at baseline settings')
    ax.legend(frameon=False, ncol=3, loc='upper center', bbox_to_anchor=(0.5, -0.12))
    plot(fig, 'benchmark-models', 'Model comparison',
         f'{folder}. A scenario passes on at least 2 of 3 runs at temperature 0; labels give passing/total scenarios. '
         'Hybrid retrieval, graph on, retries on. Judge gemma3:12b.')

    # chat retrieval modes
    fig, ax = plt.subplots(figsize=(8, 3.8))
    width = 0.16
    for i, (label, name) in enumerate(MODES):
        path = finished(f'qwen2.5_14b-{name}')
        counts = [passes(path, 'chat', chat_passed, tag) for tag, _ in GROUPS]
        bars = ax.bar([x + (i - 2) * width for x in range(len(GROUPS))], [100 * a / n for a, n in counts], width,
                      label=label.replace('\n', ' '), color=COLORS[i])
        ax.bar_label(bars, labels=[f'{a}' for a, _ in counts], fontsize=7, padding=2)
    totals = [passes(finished('qwen2.5_14b-baseline'), 'chat', chat_passed, tag)[1] for tag, _ in GROUPS]
    ax.set_xticks(range(len(GROUPS)), [f'{g} (n={n})' for (_, g), n in zip(GROUPS, totals)])
    ax.set(ylim=(0, 115), ylabel='Scenarios passing facts (%)', title='Chat retrieval modes · qwen2.5:14b')
    ax.legend(frameon=False, ncol=5, loc='upper center', bbox_to_anchor=(0.5, -0.12), fontsize=8)
    plot(fig, 'benchmark-retrieval', 'Chat retrieval modes',
         f'{folder}. Fact pass by preregistered scenario group, majority of 3 runs; bar labels are passing scenario counts. '
         'Shuffled keeps the graph facts\' length but breaks their link to the question. Temporal scenarios were written by the graph\'s builder.')

    # budget improvement
    series = [('No AI', base / 'improvement-baseline/no-ai/results.json')]
    series += [(m.replace('_', ':'), base / f'improvement-replay/{m}/results.json') for m in MODELS]
    data = []
    for name, path in series:
        track(path)
        data.append((name, json.loads(path.read_text())))
    users = [r['user'] for r in data[0][1]['results']]
    fig, axes = plt.subplots(1, len(users), figsize=(9, 3.2), sharey=False)
    for ax, user in zip(axes, users):
        for i, (name, d) in enumerate(data):
            r = next(x for x in d['results'] if x['user'] == user)
            months = [p['start'][5:7] for p in r['periods']]
            # same colour per model as the model chart; distinct markers, because two models can coincide exactly
            ax.plot(range(len(months)), [p['mape'] for p in r['periods']], marker='osD^'[i], markersize=(5, 9, 6, 5)[i],
                    markerfacecolor='none' if i == 1 else None, color=COLORS[i - 1] if i else '#17202a',
                    linestyle='--' if i == 0 else '-', label=name)
            ax.set_xticks(range(len(months)), [{'08': 'Aug', '09': 'Sep', '10': 'Oct'}.get(m, m) for m in months])
        ax.set_title(user.replace('improve-', ''))
    axes[0].set_ylabel('Budget error (%) · lower is better')
    axes[-1].legend(frameon=False, fontsize=8)
    plot(fig, 'benchmark-improvement', 'Budget improvement over periods',
         f'{folder}. One pass per model over three closed periods; the dashed line is the close logic with no model. '
         'Proposals were applied under a human approve-all policy, not the preregistered rubric. '
         'On steady, qwen2.5:14b and llama3.1:8b give identical errors, so their lines overlap (hollow squares are qwen2.5:14b).')
