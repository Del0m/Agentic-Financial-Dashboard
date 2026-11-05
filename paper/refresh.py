"""Refresh local paper figures. Run: python3 paper/refresh.py"""
import ast, hashlib, html, json, os, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', tempfile.gettempdir()+'/plaid-paper-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'text.parse_math':False,'svg.fonttype':'none','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
OUT=ROOT/'paper/generated'
OUT.mkdir(parents=True,exist_ok=True)
CONFIG=ROOT/'paper/figures.json'
config=json.loads(CONFIG.read_text())
entries=[]
sources={}
def track(p):
    sources[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
    track(p)
    return json.loads(p.read_text())
def record(name,title,caption):
    entries.append(dict(name=name,title=title,caption=caption))
def plot(fig,name,title,caption,tight=True):
    if tight:
        fig.tight_layout()
    for ext in ('svg','png','pdf'):
        fig.savefig(OUT/f'{name}.{ext}',dpi=220,bbox_inches='tight')
    plt.close(fig)
    record(name,title,caption)
def dot(name,title,lines,caption):
    p=OUT/f'{name}.dot'
    p.write_text('\n'.join(['digraph G {','rankdir=LR; bgcolor="white"; pad=0.3;',
      'node [shape=box,style="rounded,filled",fillcolor="#eaf2f8",fontname="Helvetica"];',
      'edge [fontname="Helvetica",fontsize=10,color="#566573"];',*lines,'}']))
    for ext in ('svg','png','pdf'):
        subprocess.run(['dot',f'-T{ext}',str(p),'-o',str(OUT/f'{name}.{ext}')],check=True)
    record(name,title,caption)
def q(value):
    return json.dumps(value, ensure_ascii=False)
from temporal_figures import generate
track(ROOT/'paper/temporal_figures.py')
generate(ROOT, track, dot, plot, q)
from example_figures import generate as generate_examples
generate_examples(ROOT, track, dot, q)

# Explicit route maps are parsed without importing application code.
for kind in ('chat','budget','close'):
    p=ROOT/f'backend/app/agents/{kind}_graph.py'; track(p)
    lines=[]
    def literal(n):
        if isinstance(n,ast.Name) and n.id in ('START','END'): return n.id.lower()
        return ast.literal_eval(n)
    for call in ast.walk(ast.parse(p.read_text())):
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute): continue
        if call.func.attr=='add_edge':
            a,b=map(literal,call.args[:2]); lines.append(f'{q(a)} -> {q(b)};')
        elif call.func.attr=='add_conditional_edges':
            if len(call.args)<3: raise ValueError(f'Missing explicit route map in {p}')
            a=literal(call.args[0])
            for n in call.args[2].elts:
                lines.append(f'{q(a)} -> {q(literal(n))} [style=dashed,label="conditional"];')
    if not lines: raise ValueError(f'No workflow edges in {p}')
    dot(f'{kind}-workflow',f'{kind.title()} agent workflow',lines,
        f'Extracted from {p.relative_to(ROOT)}. Dashed edges are conditional routes; these are declared paths, not execution frequencies.')
graph=read(ROOT/'kb/graph.json'); focus=config['graph_focus']
labels={c['key']:c['label'] for c in graph['concepts']}
labels.update({s['slug']:s['title'] for s in graph['sources']})
edges=[e for e in graph['edges'] if focus in (e['src'],e['dst'])]
if not edges: raise ValueError(f'No edges for {focus}')
import textwrap
lines=[f'{q(n)} [label={q(textwrap.fill(labels[n],28))}];' for n in sorted({e[k] for e in edges for k in ('src','dst')})]
for e in edges:
    label=e['rel']
    if e['rel']!='MENTIONED_IN': label+='\n'+e['valid_from']+' → '+str(e.get('valid_to') or 'open')
    lines.append(f'{q(e["src"])} -> {q(e["dst"])} [label={q(label)}];')
dot('guidance-neighborhood','Reviewed guidance graph',lines,
    f'All one-hop edges around {focus} in kb/graph.json. Dates are stored validity intervals. Open means no recorded end; this is a guidance snapshot, not live financial data.')
for i,folder in enumerate(config['runs']):
    p=ROOT/folder; run=read(p/'config.json'); summary=read(p/'summary.json')
    if not run.get('finished_at'): raise ValueError(f'Unfinished run: {folder}')
    metrics=[('Chat facts',summary.get('overall',{}),'fact_pass_rate'),('Budget checks',summary.get('budget',{}),'pass_rate'),('Goal checks',summary.get('goal',{}),'pass_rate')]
    metrics=[(label,d,k) for label,d,k in metrics if d.get(k) is not None]
    fig,ax=plt.subplots(figsize=(7,3.6)); vals=[d[k]*100 for _,d,k in metrics]
    bars=ax.barh([f'{label} (n={d["runs"]})' for label,d,_ in metrics],vals,color=['#21618c','#148f77','#af601a'][:len(vals)])
    ax.bar_label(bars,labels=[f'{v:g}%' for v in vals],padding=5)
    ax.set(xlim=(0,115),xlabel='Passing runs (%)',title=f'{run["model"]} · {run.get("label","evaluation")}')
    ax.set_xticks([0,25,50,75,100]); ax.invert_yaxis()
    plot(fig,f'evaluation-{i+1}',f'{run["model"]} evaluation snapshot',
      f'{folder}. Run-level rates, not majority-of-three scenario verdicts. Retrieval: {run.get("retrieval")}; graph: {run.get("kg_mode")}; commit: {run.get("commit")}. Each panel describes its own sample; cross-run comparability is not assumed.')
if config.get('benchmark'):
    from benchmark_figures import generate as generate_benchmark
    track(ROOT/'paper/benchmark_figures.py')
    generate_benchmark(ROOT, track, plot, config['benchmark'])
p=ROOT/config['budget_accuracy']; data=read(p); profiles=data['profiles']
fig,ax=plt.subplots(figsize=(8,4))
for offset,method,color in [(-.2,'median','#21618c'),(.2,'mean','#148f77')]:
    ax.bar([i+offset for i in range(len(profiles))],[p[method]['mape'] for p in profiles],width=.4,label=method.title(),color=color)
ax.set_xticks(range(len(profiles)),[p['id'] for p in profiles],rotation=25,ha='right')
ax.set(ylabel='Mean absolute percentage error (%)',title='Initial budget accuracy · lower is better'); ax.legend(frameon=False)
plot(fig,'budget-accuracy','Initial budget accuracy',f'{config["budget_accuracy"]}. Synthetic profiles: May–July fit, August holdout. Percentage error excludes zero-actual categories; this is initial budgeting, not longitudinal AI improvement.')
track(CONFIG); track(Path(__file__).resolve())
(OUT/'manifest.json').write_text(json.dumps(dict(figures=entries,source_sha256=sources,selection=config),indent=2)+'\n')
cards=[]
for e in entries:
    name=e['name']; links=' · '.join(f'<a href="{name}.{ext}">{ext.upper()}</a>' for ext in ('svg','pdf','png'))
    cards.append(f'<article><h2>{html.escape(e["title"])}</h2><img src="{name}.svg"><p>{html.escape(e["caption"])}</p>{links}</article>')
(OUT/'index.html').write_text('''<!doctype html><meta charset="utf-8"><title>Plaid Agent paper figures</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 24px;background:#f4f6f7;color:#17202a}article{background:white;padding:28px;margin:24px 0;border-radius:12px}img{max-width:100%;max-height:700px}p{line-height:1.6;color:#475569}a{color:#21618c}</style><h1>Plaid Agent · paper figures</h1><p>Selected local snapshots. Source hashes and captions: <a href="manifest.json">manifest</a>.</p>'''+''.join(cards))
print(f'Generated {len(entries)} figures: {OUT / "index.html"}')
