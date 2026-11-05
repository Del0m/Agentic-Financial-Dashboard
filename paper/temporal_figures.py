"""Paper views of the implemented ontology and independent temporal test fixture."""
import ast
from datetime import date, datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch


def generate(root, track, dot, plot, q):
    # Read the enforced endpoint schema, rather than maintaining a second ontology.
    path = root / 'backend/app/kg/store.py'
    track(path)
    tree = ast.parse(path.read_text())
    candidates = [n.value for n in tree.body if isinstance(n, ast.Assign)
                  and isinstance(n.value, ast.Dict)
                  and any(isinstance(k, ast.Constant) and k.value == 'SUBSCRIBES' for k in n.value.keys)]
    if len(candidates) != 1:
        raise ValueError('Cannot identify the enforced graph relation schema')
    schema = ast.literal_eval(candidates[0])
    personal = {'user', 'account', 'institution', 'merchant', 'category', 'stream', 'advice'}
    nodes = sorted({kind for pairs in schema.values() for pair in pairs for kind in pair})
    lines = [f'{q(n)} [label={q(n.title())}, fillcolor={q("#eaf2f8" if n in personal else "#d5f5e3")}];' for n in nodes]
    for rel, pairs in sorted(schema.items()):
        for src, dst in sorted(pairs):
            lines.append(f'{q(src)} -> {q(dst)} [label={q(rel)}];')
    dot('temporal-knowledge-graph', 'Temporal knowledge graph · implemented schema', lines,
        'Node types and allowed relationships read from backend/app/kg/store.py. Blue: personal financial entities; green: shared guidance. This is the complete allowed relationship schema, not a populated user snapshot. Every edge carries valid_from/valid_to and recorded_at/expired_at; nodes retain their current display labels.')

    # Parse only a small whitelist of literal fixture expressions. Never execute test code.
    path = root / 'backend/tests/kg_query_fixtures.py'
    track(path)
    track(root / 'backend/app/db/models.py')
    track(root / 'backend/app/kg/query.py')
    tree = ast.parse(path.read_text())
    times = {}
    def value(n):
        if isinstance(n, ast.Name) and n.id in times:
            return times[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ('date', 'datetime'):
            args = [ast.literal_eval(a) for a in n.args]
            if n.func.id == 'date':
                return date(*args)
            if ast.unparse(n.keywords[0].value) != 'timezone.utc':
                raise ValueError('Expected UTC fixture timestamps')
            return datetime(*args, tzinfo=timezone.utc)
        return ast.literal_eval(n)
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ('T1','T2','T3','T4'):
            times[n.targets[0].id] = value(n.value)
    oracle = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'oracle')
    rows_expr = next(n.value for n in oracle.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'rows' for t in n.targets))
    rows = []
    for call in rows_expr.elts:
        fields = {kw.arg: value(kw.value) for kw in call.keywords}
        if fields['rel'] != 'SUBSCRIBES':
            raise ValueError('Temporal example expects subscription edges')
        rows.append(fields)
    if len(rows) != 5:
        raise ValueError('Temporal fixture changed; review the explanatory layout')

    fig = plt.figure(figsize=(13, 8))
    grid = fig.add_gridspec(3, 2, height_ratios=[1.1, 3, .65], hspace=.55, wspace=.28)
    header = fig.add_subplot(grid[0, :]); header.axis('off')
    header.text(.5, .92, 'One relationship, five retained versions', ha='center', fontsize=17, weight='bold')
    for x, label in [(.18, 'Alice\nuser'), (.82, 'Gym\nrecurring stream')]:
        header.text(x, .30, label, ha='center', va='center', fontsize=12,
                    bbox=dict(boxstyle='round,pad=.65', fc='#eaf2f8', ec='#21618c'))
    header.add_patch(FancyArrowPatch((.29,.30),(.69,.30),arrowstyle='-|>',mutation_scale=15,color='#21618c'))
    header.text(.5,.44,'SUBSCRIBES · typical_cents',ha='center',fontsize=11)
    titles = ['Valid time · when the price applies', 'Recorded time · when this version was known']
    colors = {3500:'#21618c',4500:'#af601a',4000:'#148f77'}
    horizon = date(2026,7,1)
    for col, (start_key,end_key) in enumerate([('start','end'),('recorded','expired')]):
        ax = fig.add_subplot(grid[1,col])
        for i,row in enumerate(rows):
            start=row[start_key]; end=row.get(end_key)
            x=mdates.date2num(start); stop=mdates.date2num(end or horizon)
            color=colors[row['value']['typical_cents']]
            ax.barh(i,stop-x,left=x,height=.42,color=color,alpha=.85)
            ax.plot(x,i,marker='o',color=color,markersize=5)
            if end is None:
                ax.plot(stop,i,marker='>',color=color,markersize=7)
            else:
                ax.plot(stop,i,marker='o',markerfacecolor='white',markeredgecolor=color,markersize=5)
        ax.set_yticks(range(len(rows)),[f'v{i+1}  ${r["value"]["typical_cents"]/100:g}' for i,r in enumerate(rows)])
        ax.invert_yaxis(); ax.set_title(titles[col],pad=14,fontsize=12)
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        ax.set_xlim(mdates.date2num(date(2026,1,1))-4,mdates.date2num(horizon)+5)
        ax.tick_params(axis='x',rotation=35); ax.grid(axis='x',alpha=.18)
    footer=fig.add_subplot(grid[2,:]); footer.axis('off')
    footer.text(0,.9,'Same valid date: April 2, 2026',fontsize=11,weight='bold')
    footer.text(0,.35,'Known April 10: $45   →   Known May 10: $40 (correction)   |   No active subscription from June 1, once recorded June 10.',fontsize=10)
    fig.suptitle('Temporal knowledge graph · synthetic regression-test example',y=1.02,fontsize=16)
    # This figure uses an explicit gridspec; avoid tight_layout on its annotation axes.
    plot(fig,'temporal-edge-history','Temporal edge history · two clocks',
        'Read directly from the Alice/Gym oracle in backend/tests/kg_query_fixtures.py, not a live account. All five retained edge versions are shown. Filled start / hollow end denote half-open intervals [start, end); arrows mean no recorded end, clipped at July 1 for display. Recorded timestamps are UTC (12:00 on the dated events). Old versions remain available for historical queries. v3 is corrected by v4; v5 records the June 1 closure on June 10. The $35 v2 remains the accepted January–March history.', tight=False)
