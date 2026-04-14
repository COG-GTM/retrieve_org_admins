# retrieve_org_admins

Fetch all GitHub organization owners across a GitHub Enterprise Server (GHES) enterprise.

## What it does

This script uses GraphQL to list all organizations in a GHES enterprise, then for each organization:
1. Lists all members using the REST API
2. Checks each member's role (admin/member)
3. Collects all users with admin role and outputs the results to JSON

## Prerequisites

- **uv** (Python package manager) - Install from https://github.com/astral-sh/uv
- A GitHub Enterprise Server instance
- A Personal Access Token (PAT) from a **site admin** account with the following scopes:
  - `admin:org` - to read organization members
  - `read:enterprise` - to access enterprise data

## Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd retrieve_org_admins
   ```

2. **Create your `.env` file**
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env` with your credentials**
   ```bash
   GHE_TOKEN=ghp_your_token_here
   GHE_HOST=your-ghe-host.com
   GHE_ENTERPRISE_SLUG=your-enterprise-slug
   ```

   Required environment variables:
   - `GHE_TOKEN` - Your GHES site admin PAT
   - `GHE_HOST` - Your GHES hostname (e.g., `github.example.com`)
   - `GHE_ENTERPRISE_SLUG` - The enterprise slug (found in your GHES URL: `/enterprises/<slug>`)

## Usage

**Run the script using uv:**

```bash
uv run get_org_owners.py
```

This command will:
- Automatically install required dependencies (python-dotenv)
- Use the correct Python version (>=3.11)
- Execute the script with your environment variables

## Expected Output

The script will:
1. Verify your token has site admin permissions
2. Fetch all organizations in your enterprise
3. Check each organization for admin members
4. Generate a JSON file at `data/ghe_org_admins.json` with the results
5. Print a summary to the console

Example output:
```
=== Checking token permissions ===
Authenticated as: username  (site_admin=True)

=== Fetching orgs from enterprise 'your-enterprise' on your-ghe-host.com ===
  Orgs page 1: fetched 10 (total: 10)
Found 10 organizations.

=== Checking org members and roles ===
  [1/10] org1: 5 member(s), 2 admin(s) ['admin1', 'admin2']
  [2/10] org2: 3 member(s), 1 admin(s) ['admin3']
  ...

=== Summary: Org Owners ===
  org1: ["admin1", "admin2"]
  org2: ["admin3"]

=== Orgs with NO identified owners (2) ===
  org3
  org4

Total: 8 orgs with owners, 2 without
Done!
```

## Output Format

The script creates `data/ghe_org_admins.json` with the following structure:

```json
[
  {
    "org_login": "org-name",
    "org_name": "Organization Display Name",
    "member_count": 10,
    "admin_count": 2,
    "admins": ["admin1", "admin2"]
  }
]
```

## Troubleshooting

**"KeyError: 'GHE_TOKEN'"**
- Ensure your `.env` file exists in the same directory as the script
- Verify all required environment variables are set

**"ERROR: This token does NOT belong to a site administrator"**
- Your PAT must be from a site admin account
- Ask a site admin to promote your account: `ghe-user-promote -u YOUR_USERNAME`

**"Could not access enterprise. Check slug and token scopes."**
- Verify your `GHE_ENTERPRISE_SLUG` is correct
- Ensure your PAT has the `read:enterprise` scope

**uv command not found**
- Install uv from https://github.com/astral-sh/uv
- On macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`