# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-dotenv",
# ]
# ///

"""
Fetch all GitHub org owners across a GHES enterprise.

Approach: use GraphQL to list enterprise orgs, then for each org:
  1. GET /orgs/:org/members — list all members (site admin can see all).
  2. GET /orgs/:org/memberships/:user — get role (admin/member) for each member.
  3. Collect all users with role=admin and output a CSV.

Required environment variables (set in .env):
  GHE_TOKEN           – A GHE PAT from a **site admin** with admin:org + read:enterprise scopes
  GHE_HOST            – The GHE hostname, e.g. mercedes-benz.ghe.com
  GHE_ENTERPRISE_SLUG – The enterprise slug (shown in /enterprises/<slug>)
"""

import json
import ssl
import os
import time as _time
import urllib.request
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GHE_TOKEN = os.environ["GHE_TOKEN"]
GHE_HOST = os.environ.get("GHE_HOST", "mercedes-benz.ghe.com")
ENTERPRISE_SLUG = os.environ["GHE_ENTERPRISE_SLUG"]

GRAPHQL_URL = f"https://{GHE_HOST}/api/graphql"
REST_BASE = f"https://{GHE_HOST}/api/v3"
MAX_RETRIES = 5
ctx = ssl.create_default_context()


# ── REST helpers ─────────────────────────────────────────────────────────────

def rest_get(url: str) -> list | dict | None:
    """GET a REST endpoint with retries."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GHE_TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code in (403, 404):
                return None
            wait = 2 ** attempt
            print(f"  HTTP {e.code}: {body[:120]}, retrying in {wait}s...")
            _time.sleep(wait)
        except Exception as e:
            wait = 2 ** attempt
            print(f"  Attempt {attempt + 1} failed ({e}), retrying in {wait}s...")
            _time.sleep(wait)
    return None


# ── GraphQL helpers ──────────────────────────────────────────────────────────

def graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against the GHE instance with retries."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"bearer {GHE_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                data = json.loads(resp.read())
            if "errors" in data:
                print(f"  GraphQL errors: {data['errors']}")
            return data
        except Exception as e:
            wait = 2 ** attempt
            print(f"  Attempt {attempt + 1} failed ({e}), retrying in {wait}s...")
            _time.sleep(wait)
    raise RuntimeError(f"GraphQL request failed after {MAX_RETRIES} attempts")


# ── Preflight ────────────────────────────────────────────────────────────────────

def check_site_admin():
    """Verify the token belongs to a GHES site admin."""
    result = rest_get(f"{REST_BASE}/user")
    if result is None:
        print("ERROR: Could not authenticate. Check GHE_TOKEN.")
        raise SystemExit(1)

    login = result.get("login", "?")
    is_site_admin = result.get("site_admin", False)
    print(f"Authenticated as: {login}  (site_admin={is_site_admin})")

    if not is_site_admin:
        print(
            "\nERROR: This token does NOT belong to a site administrator.\n"
            "A site admin token is required so that /orgs/:org/members\n"
            "returns members for orgs you don't belong to.\n\n"
            "To fix this, either:\n"
            "  1. Use a PAT from a GHES site admin account, or\n"
            "  2. Have a site admin run:  ghe-user-promote -u YOUR_USERNAME\n"
        )
        raise SystemExit(1)

    return login


# ── Step 1: Fetch all enterprise orgs via GraphQL ────────────────────────

ORGS_QUERY = """
query($slug: String!, $cursor: String) {
  enterprise(slug: $slug) {
    organizations(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { login name }
    }
  }
}
"""


def fetch_all_orgs() -> list[dict]:
    """Return all orgs in the enterprise."""
    orgs = []
    cursor = None
    page = 0
    while True:
        page += 1
        result = graphql(ORGS_QUERY, {"slug": ENTERPRISE_SLUG, "cursor": cursor})
        enterprise = result.get("data", {}).get("enterprise")
        if enterprise is None:
            print("ERROR: Could not access enterprise. Check slug and token scopes.")
            break
        org_conn = enterprise["organizations"]
        batch = org_conn["nodes"]
        orgs.extend(batch)
        print(f"  Orgs page {page}: fetched {len(batch)} (total: {len(orgs)})")
        if org_conn["pageInfo"]["hasNextPage"]:
            cursor = org_conn["pageInfo"]["endCursor"]
        else:
            break
    return orgs


# ── Step 2: For each org, list members and check roles ────────────────────

def fetch_org_members(org_login: str) -> list[dict]:
    """GET /orgs/:org/members — list all members. Works for site admins."""
    members = []
    page = 1
    while True:
        url = f"{REST_BASE}/orgs/{urllib.parse.quote(org_login, safe='')}/members?per_page=100&page={page}"
        batch = rest_get(url)
        if batch is None or not isinstance(batch, list) or len(batch) == 0:
            break
        members.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return members


def get_membership_role(org_login: str, user_login: str) -> str | None:
    """GET /orgs/:org/memberships/:user — returns 'admin' or 'member'."""
    url = f"{REST_BASE}/orgs/{urllib.parse.quote(org_login, safe='')}/memberships/{urllib.parse.quote(user_login, safe='')}"
    result = rest_get(url)
    if result is None or not isinstance(result, dict):
        return None
    return result.get("role")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    output_dir = ROOT / "data"
    output_dir.mkdir(exist_ok=True)

    # Preflight
    print("=== Checking token permissions ===")
    check_site_admin()
    print()

    # Step 1: Get all enterprise orgs
    print(f"=== Fetching orgs from enterprise '{ENTERPRISE_SLUG}' on {GHE_HOST} ===")
    orgs = fetch_all_orgs()
    print(f"Found {len(orgs)} organizations.\n")

    # Step 2: For each org, list members and check their roles
    print("=== Checking org members and roles ===")
    org_rows: list[dict] = []
    orgs_without_owners: list[str] = []

    for i, org in enumerate(orgs):
        org_login = org["login"]
        org_name = org.get("name") or ""

        members = fetch_org_members(org_login)
        admins = []

        for member in members:
            user_login = member["login"]
            role = get_membership_role(org_login, user_login)
            if role == "admin":
                admins.append(user_login)

        org_rows.append({
            "org_login": org_login,
            "org_name": org_name,
            "member_count": len(members),
            "admin_count": len(admins),
            "admins_json": json.dumps(sorted(admins)),
        })

        if not admins:
            orgs_without_owners.append(org_login)

        print(f"  [{i + 1}/{len(orgs)}] {org_login}: {len(members)} member(s), {len(admins)} admin(s) {admins}")

    # Write JSON
    output_file = output_dir / "ghe_org_admins.json"
    output_data = []
    for row in sorted(org_rows, key=lambda r: r["org_login"]):
        output_data.append({
            "org_login": row["org_login"],
            "org_name": row["org_name"],
            "member_count": row["member_count"],
            "admin_count": row["admin_count"],
            "admins": json.loads(row["admins_json"]),
        })
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nWrote {len(output_data)} org entries to {output_file}")

    # Summary
    print("\n=== Summary: Org Owners ===")
    for row in sorted(org_rows, key=lambda r: r["org_login"]):
        if row["admin_count"] > 0:
            print(f"  {row['org_login']}: {row['admins_json']}")

    if orgs_without_owners:
        print(f"\n=== Orgs with NO identified owners ({len(orgs_without_owners)}) ===")
        for org_login in sorted(orgs_without_owners):
            print(f"  {org_login}")

    orgs_with_owners = sum(1 for r in org_rows if r["admin_count"] > 0)
    print(f"\nTotal: {orgs_with_owners} orgs with owners, {len(orgs_without_owners)} without")
    print("Done!")


if __name__ == "__main__":
    main()
