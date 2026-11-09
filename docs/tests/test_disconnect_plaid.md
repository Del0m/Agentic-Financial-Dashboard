# Sandbox disconnect tests

`backend/tests/test_disconnect_plaid.py` exercises `POST /plaid/disconnect` with explicit `confirmed: true`.

- Only the configured Sandbox demo user with admin tools enabled can disconnect.
- Cleanup revokes the Item, resets the local ledger and derived data, changes user identity to isolate prior Chat threads, and returns to an unlinked Dashboard. Other users and shared guidance survive.
- Missing confirmation, active agent/Chat/sync work, missing credentials, backup failure and provider failure do not clear local data.
- A previously revoked Item can finish local cleanup on retry.
- A file-backed test verifies a recoverable SQLite snapshot exists before the mocked remote revocation occurs. Other reset tests cover foreign-key deletion order and private file permissions.

Dashboard tests cover opening/cancelling confirmation, confirmed success, an API refusal and hidden controls when unavailable. They never disconnect the live demo bank.

Old checkpoint and audit files remain on disk. The backup preserves historical data, but cannot restore a token revoked at Plaid; reconnect through Link to establish a new connection. The local CLI reset and UI disconnect share cleanup/backup code, but only the UI route revokes the remote Item. One API worker is required, as for the existing per-item sync and Chat locks.
