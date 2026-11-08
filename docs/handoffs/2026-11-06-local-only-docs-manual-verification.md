# Local-only docs: manual verification

Some docs now stay on disk but out of the repo. They're listed in `.gitignore` and are no longer tracked:

- `docs/handoffs/2026-11-06-demo-purchases.md` (never committed)
- `docs/handoffs/2026-11-05-tpc-28-results-manual-verification.md`
- `docs/diagrams/README.md` and `docs/diagrams/paper-figures.tex` (never committed)
- `paper/` (the figure scripts, `figures.json`, README and `generated/`)

Lines were edited in `README.md`, `docs/agent-pipelines.md` (one section removed, and the sections after it renumbered), `docs/system-walkthrough.md`, `docs/architecture.md`, `docs/handoffs/2026-11-02-tpc-42-manual-verification.md`, `docs/superpowers/specs/2026-11-02-tpc-24-goal-validation-design.md` and `docs/superpowers/specs/2026-11-01-docker-dev-design.md`.

## Check it

1. Run `git status --short`. Every `paper/` file and the TPC-28 handoff should show as `D` (staged removal). None of the listed files should show as `??`.
2. Run `ls paper docs/diagrams/README.md docs/handoffs/2026-11-06-demo-purchases.md`. The files are still on disk.
3. Run `git check-ignore -v paper/README.md docs/diagrams/README.md docs/diagrams/paper-figures.tex docs/handoffs/2026-11-06-demo-purchases.md`. Each one prints the `.gitignore` line that matches it.
4. Run `git diff -- README.md docs/`. You should see only the edited lines described above.
5. In `docs/system-walkthrough.md`, follow the link to "Evaluation evidence for the writeup". It should land on section 6 of `docs/agent-pipelines.md`.
6. Run `python3 paper/refresh.py`. It still works from the local copy.

## Limits

The files removed from tracking are still in git history at earlier commits. Removing them from history would mean rewriting it, which hasn't been done. The removal only takes effect for others once it's committed and pushed.
