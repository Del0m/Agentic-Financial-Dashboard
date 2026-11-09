"""Link a Sandbox bank end to end, without the frontend, then sync it.

Gets a Sandbox public_token straight from Plaid, then exchanges it through our
running API, the same call the frontend makes after Link succeeds. Then syncs
the new item twice: the second run should add nothing (sync is idempotent).

Run with the API up (uvicorn app.main:app), from any directory:
    python backend/scripts/sandbox_link.py [base_url] [--dynamic]

--dynamic links Plaid's user_transactions_dynamic test user, the one that takes new transactions, so
scripts/demo_purchase.py --plaid can send purchases through it. The bank goes to the dev user (dev@localhost, the
app's "Plaid bank" view) even while the app shows the demo user.
"""
import argparse
import sys
import time
from pathlib import Path

import httpx

# make `app` importable however the script is launched (it lives in backend/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.plaid_service.client import DYNAMIC_USER, sandbox_public_token

parser = argparse.ArgumentParser(description="Link a Plaid Sandbox bank without the frontend, then sync it.")
parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
parser.add_argument("--dynamic", action="store_true", help=f"link {DYNAMIC_USER}, which takes new transactions")
args = parser.parse_args()
base_url = args.base_url
# the "Plaid bank" view: with the admin tools on, this cookie makes the api act for the dev user, not the demo user
client = httpx.Client(cookies={"view": "plaid"})

resp = client.post(f"{base_url}/plaid/link-token", timeout=30)
if resp.status_code != 200:
    sys.exit(f"FAILED link-token ({resp.status_code}): {resp.text}")
print(f"OK: link_token = {resp.json()['link_token'][:20]}...")

public_token = sandbox_public_token(username=DYNAMIC_USER if args.dynamic else None)
print(f"OK: sandbox public_token = {public_token[:20]}...")

resp = client.post(f"{base_url}/plaid/exchange", json={"public_token": public_token, "institution_name": "First Platypus Bank"},
                   timeout=30)
if resp.status_code != 200:
    sys.exit(f"FAILED exchange ({resp.status_code}): {resp.text}")
print(f"OK: stored plaid_items row id = {resp.json()['item_id']}")
item_id = resp.json()["item_id"]


def sync() -> dict:
    resp = client.post(f"{base_url}/plaid/items/{item_id}/sync", timeout=60)
    if resp.status_code != 200:
        sys.exit(f"FAILED sync ({resp.status_code}): {resp.text}")
    return resp.json()


# a new sandbox item loads its history over several seconds, so keep syncing until a run
# adds nothing. each run only pulls what's new since the last cursor.
total = 0
for _ in range(15):
    time.sleep(2)
    body = sync()
    total += body["transactions"]["added"]
    print(f"OK: sync {body['transactions']}")
    if body["transactions"]["added"] == 0 and total > 0:
        break
print(f"OK: {total} transactions across {body['accounts']} accounts; last run added 0 (no duplicates)")
for liab in body["liabilities"]:
    print(f"    {liab['kind']} liability, APR {liab['apr']}")
