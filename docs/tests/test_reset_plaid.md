# Tests: Plaid Sandbox reset

`backend/tests/test_reset_plaid.py` verifies the destructive cleanup in `scripts/reset_plaid.py` using isolated databases.

- All Plaid-user dependent rows can be deleted with foreign keys enabled, including advice, goals, streams, budgets and personal graph edges. The seeded user and shared guidance survive.
- The recreated user has a new identity, separating its Chat checkpoint namespace from earlier conversations.
- Preview makes no changes. Production/development environments, simulated users, active claims/runs and non-Sandbox tokens are refused.
- Backup failure prevents deletion. A file-backed reset creates a readable pre-reset SQLite backup with owner-only permissions.

Run: `docker compose exec backend python -m pytest tests/test_reset_plaid.py -q`.

The tests do not revoke Items at Plaid, link a new bank or reset the live demo database. Stop the backend before applying the command; concurrent Chat/background-sync writes are outside its database run-claim check. Old checkpoint files and prompt logs are retained on disk.
