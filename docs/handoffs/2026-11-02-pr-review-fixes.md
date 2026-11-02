# PR review fixes

Changes cover the review of PRs #18–22 across the branches listed below. No pushes or development database migrations were performed.

## Runtime changes

- Run `alembic upgrade head` before starting the updated backend. The new `user_run_claims` table coordinates per-user run ownership across API, CLI, scheduler, and simulation entrypoints. Claims expire after five minutes and renew every 30 seconds. Goal replacement and withdrawal verify ownership within their write transaction. Startup recovery preserves runs with a live claim; demo reset refuses an active claim.
- Goal planning fails closed with `incomplete_card_obligations` when a credit account has an unknown balance or a positive balance without a known minimum payment. Existing proposals remain intact and no model call is made.
- Arbitrary preference notes are never forwarded to the model or model audit. Only fixed emergency-fund, debt-payoff, or savings priority hints and explicit avalanche/snowball strategy hints are extracted. Unsupported text becomes a fixed fallback; exclusions should use structured `skip_kinds`. Raw preferences remain stored locally.
- Goal failures log exception types without raw exception messages or SQL parameters. Attempt counts and validation history survive persistence failures.
- Payoff interest uses exact integer arithmetic and half-up rounding, including non-amortizing high-APR debts. Determining available goal kinds no longer runs a baseline payoff simulation.
- Historical windows use the earliest actual transaction date separately from the fetched fit window, preserving a full first month even when the preceding month is quiet.
- Simulated users cannot create bank links. Simulation and demo replacement reject users with real bank links. Goal demo profiles support advancing simulated dates.
- Both Compose frontends bind to `127.0.0.1`. The Docker bootstrap preserves existing effective Fernet keys, including exported and indented dotenv assignments.

## Working copies

- Main checkout: goal, payoff, history, shared bank-boundary, and run-ownership fixes.
- `.claude/worktrees/TPC-42`: Docker setup and bank-boundary fixes.
- `.claude/worktrees/jolly-lamport-5f927a`: history boundary fix.

## Validation

Tests run in isolated temporary source copies with a clean environment, not against the development database. Regression coverage includes concurrent goal processes, expired ownership, API restart recovery, missing card obligations, privacy failures, payoff overflow, synthetic-user boundaries, dotenv key preservation, and quiet history months. Live-model and Plaid sandbox tests are excluded from these runs.

Final results: main checkout 824 passed, Docker checkout 709 passed, history checkout 643 passed.
