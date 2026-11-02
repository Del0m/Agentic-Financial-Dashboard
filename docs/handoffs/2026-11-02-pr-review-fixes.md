# PR review fixes

Changes cover the review of PRs #18–22 across the branches listed below. No development database migrations were performed.

## Runtime changes

- Run `alembic upgrade head` before starting the updated backend. The new `user_run_claims` table coordinates per-user run ownership across API, CLI, scheduler, and simulation entrypoints. Claims expire after five minutes and renew every 30 seconds. Goal replacement and withdrawal verify ownership within their write transaction. Startup recovery preserves runs with a live claim; demo reset refuses an active claim.
- Goal planning fails closed with `incomplete_card_obligations` when a credit account has an unknown balance or a positive balance without a known minimum payment. Existing proposals remain intact and no model call is made.
- Arbitrary preference notes are never forwarded to the model or model audit. Only fixed emergency-fund, debt-payoff, or savings priority hints and explicit avalanche/snowball strategy hints are extracted. Unsupported text becomes a fixed fallback; exclusions should use structured `skip_kinds`. Raw preferences remain stored locally.
- Goal failures log exception types without raw exception messages or SQL parameters. Attempt counts and validation history survive persistence failures.
- Payoff interest uses exact integer arithmetic and half-up rounding, including non-amortizing high-APR debts. Determining available goal kinds no longer runs a baseline payoff simulation.
- Historical windows use the earliest actual transaction date separately from the fetched fit window, preserving a full first month even when the preceding month is quiet.
- Simulated users cannot create bank links. Simulation and demo replacement reject users with real bank links. Goal demo profiles support advancing simulated dates.
- Both Compose frontends bind to `127.0.0.1`. The Docker bootstrap preserves existing effective Fernet keys, including exported and indented dotenv assignments.

## PR branches

- #19 `TPC-32`: high-APR payoff arithmetic and loopback frontend binding.
- #18 `TPC-42`: inherits #19 and adds dotenv key preservation and synthetic-bank boundaries.
- #20 `TPC-23`: inherits #19 and adds goal privacy, missing-obligation checks, run ownership, simulation, and history fixes.
- #21 `codex/tpc-24-goal-validation`: inherits #20 and retains validation/retry-specific fixes, including progress reporting after failed persistence.
- #22 `claude/jolly-lamport-5f927a`: independent history boundary correction.

## Validation

Tests run in isolated temporary source copies with a clean environment, not against the development database. Regression coverage includes concurrent goal processes, expired ownership, API restart recovery, missing card obligations, privacy failures, payoff overflow, synthetic-user boundaries, dotenv key preservation, and quiet history months. Live-model and Plaid sandbox tests are excluded from these runs.

Final per-branch results: #19 676 passed, #18 710 passed, #20 790 passed, #21 825 passed, #22 643 passed.

## Integration rehearsal

The fixed branches were combined in a disposable checkout: 863 tests passed, with 22 live/sandbox tests deselected. Two conflicts require retaining both fixes: use the goal branch's `delete_user` guard (bank-link plus active-run protection) when combining Docker and goals, and retain both sets of architecture decision rows when merging #22. No PR was merged into the real main branch during this rehearsal.
