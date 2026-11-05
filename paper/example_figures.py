"""Small, explicitly synthetic graph instances for explaining retrieval in the paper."""
import ast
import json


def generate(root, track, dot, q):
    track(root / 'paper/example_figures.py')
    for name in ('project.py', 'facts.py', 'store.py'):
        track(root / 'backend/app/kg' / name)
    tree = ast.parse((root / 'backend/app/kg/store.py').read_text())
    schema = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == '_PAIRS' for t in n.targets))

    def render(name, title, question, takeaway, nodes, edges, caption):
        heading = question + '\nIllustrative example · synthetic personal data'
        lines = [f'graph [label={q(heading)}, labelloc="t", fontsize=18, fontname="Helvetica", nodesep=0.5, ranksep=1.0];']
        kinds = {key: kind for key, kind, label in nodes}
        for key, kind, label in nodes:
            fill = '#d5f5e3' if kind in ('concept', 'source') else '#eaf2f8'
            lines.append(f'{q(key)} [label={q(label)},fillcolor={q(fill)}];')
        for src, rel, dst, detail in edges:
            if (kinds[src], kinds[dst]) not in schema[rel]:
                raise ValueError(f'Example relationship violates ontology: {src} {rel} {dst}')
            label = rel + ('\n' + detail if detail else '')
            lines.append(f'{q(src)} -> {q(dst)} [label={q(label)}];')
        dot(name, title, lines, 'Synthetic teaching example. ' + caption + ' How it is used: ' + takeaway)

    render('example-price-change', 'Example 1 · explaining a subscription increase',
        'Question: What changed in my gym subscription?',
        'Compare dated SUBSCRIBES facts: $35 to $40 is a $5 increase. The merchant link identifies the charge. These are two valid-time versions of one relationship, not two simultaneous subscriptions.',
        [('alice','user','Alice\nuser'), ('gym-stream','stream','Gym membership\nrecurring stream'), ('gym','merchant','Neighborhood Gym\nmerchant')],
        [('alice','SUBSCRIBES','gym-stream','$35 / month\nvalid: Jan 1–Apr 1, 2026'),
         ('alice','SUBSCRIBES','gym-stream','$40 / month\nvalid: Apr 1–Jun 1, 2026'),
         ('gym-stream','SUBSCRIBES','gym','merchant identity')],
        'Illustrative accepted history after a correction; intervals exclude their end date. The adjacent two-clock figure shows how a $45 report was corrected to $40. Price-change facts are supported by backend/app/kg/facts.py.')

    render('example-budget-context', 'Example 2 · gathering context for a budget diagnosis',
        'Question: Why is this category over budget?',
        'The monthly spending total ($90) exceeds the $80 limit by $10. Recurring-charge history and the prior dismissed suggestion provide context for diagnosis; the graph alone does not establish the cause or select an action.',
        [('alice','user','Alice\nuser'), ('ent','category','Entertainment\ncategory'),
         ('gym','merchant','Neighborhood Gym\nmerchant'), ('stream','stream','Gym membership\nrecurring stream'),
         ('advice','advice','Earlier suggestion\nreduce discretionary spending')],
        [('alice','BUDGETS','ent','$80 limit\nApril 2026'),
         ('alice','SPENDS_AT','gym','$90 total · 2 charges\nApril 2026'),
         ('gym','CATEGORIZED_AS','ent','effective Apr 1, 2026'),
         ('alice','SUBSCRIBES','stream','$40 typical recurring charge\nfrom Apr 1, 2026'),
         ('stream','SUBSCRIBES','gym','merchant identity'),
         ('advice','ABOUT','ent','action: suggest_cuts\noutcome: dismissed\nvalid: Mar 15–16, 2026')],
        'Amounts and entities are invented to illustrate the implemented projection and budget-context relationships. Monthly totals, limits, recurring amounts, and advice outcomes are edge values. A single dismissal does not trigger the repeated-dismissal suppression rule.')

    graph_path = root / 'kb/graph.json'; track(graph_path)
    graph = json.loads(graph_path.read_text())
    rule = next(e for e in graph['edges'] if e['src']=='roth-ira' and e['rel']=='HAS_RULE' and e['dst']=='after-tax-contribution')
    source = rule['evidence']['source']
    provenance = next(e for e in graph['edges'] if e['src']=='roth-ira' and e['rel']=='MENTIONED_IN' and e['dst']==source)
    labels = {c['key']: c['label'] for c in graph['concepts']}
    labels.update({s['slug']: s['title'] for s in graph['sources']})
    import textwrap
    render('example-guidance-path', 'Example 3 · connecting an account to sourced guidance',
        'Question: What guidance is relevant to this account?',
        'The account type links a personal account to a shared concept. That concept connects to a stored rule and its source, providing a retrieval path for an explanation with provenance. This is not a claim that every query traverses this exact path.',
        [('alice','user','Alice\nuser'), ('account','account','Retirement account\nsubtype: roth'),
         ('roth-ira','concept',labels['roth-ira']+'\nshared concept'),
         ('after-tax-contribution','concept',textwrap.fill(labels['after-tax-contribution'],24)),
         (source,'source',textwrap.fill(labels[source],28))],
        [('alice','HAS_ACCOUNT','account','personal ownership'),
         ('account','IS_A','roth-ira','account subtype bridge'),
         ('roth-ira','HAS_RULE','after-tax-contribution','stored rule\nvalid from '+rule['valid_from']),
         ('roth-ira',provenance['rel'],source,'source provenance')],
        'Alice and her account are synthetic. Green guidance nodes and their two relationships come from the current kb/graph.json snapshot; this is a demonstration of stored evidence, not a current tax-guidance verification. The account bridge follows SUBTYPE_CONCEPTS in project.py.')

    # The same valid date, queried at three recording cutoffs from the test oracle.
    track(root / 'backend/tests/kg_query_fixtures.py')
    track(root / 'backend/tests/test_kg_query.py')
    render('example-temporal-snapshots', 'Example 4 · the same past date, different knowledge',
        'Question: What was the gym price on April 2, 2026?',
        'Hold the valid date fixed at April 2 and change known_at. Before the increase was recorded, the graph returned $35. After the report it returned $45. After the correction it returned $40. Earlier versions remain queryable rather than being overwritten.',
        [('a1','user','Alice\nknown at Jan 11, 2026'),
         ('g1','stream','Gym membership\nanswer: $35'),
         ('a2','user','Alice\nknown at Apr 10, 2026, 12:00 UTC'),
         ('g2','stream','Gym membership\nanswer: $45'),
         ('a3','user','Alice\nknown at May 10, 2026, 12:00 UTC'),
         ('g3','stream','Gym membership\nanswer: $40')],
        [('a1','SUBSCRIBES','g1','$35 · version 1\nvalid: Jan 1 onward\nrecorded: Jan 10–Apr 10'),
         ('a2','SUBSCRIBES','g2','$45 · version 3\nvalid: Apr 1 onward\nrecorded: Apr 10–May 10'),
         ('a3','SUBSCRIBES','g3','$40 · version 4\nvalid: Apr 1 onward\nrecorded: May 10–Jun 10')],
        'Three views of the SAME Alice→Gym relationship, not three users or subscriptions. Values and dates illustrate the existing Alice/Gym query-test oracle. All dates are in 2026; recording boundaries are at 12:00 UTC. Intervals exclude their end. The January knowledge view is a historical belief about April, not a guaranteed forecast. “Onward” describes what that retained version asserted at the time; the later closure is shown in the two-clock timeline.')
