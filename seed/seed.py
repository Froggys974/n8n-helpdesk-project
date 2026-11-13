#!/usr/bin/env python3
"""Seed the HelpDesk Baserow database with demo data.

Replayable: every row is upserted on a business key (email, title, reference),
so running the script twice never creates duplicates. Seeded tickets get their
status and SLA due date reset, which makes the W3 (SLA) demo repeatable.

Usage:
    python3 seed/seed.py            # reads BASEROW_URL / BASEROW_TOKEN from .env
    python3 seed/seed.py --dry-run  # show what would change, write nothing

Requires a Baserow *database token* (Settings > Database tokens) with
create/read/update/delete rights on the HelpDesk database. Python stdlib only.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"

# table name in Baserow -> (data file, upsert key field)
TABLES = {
    "Collaborators": ("collaborators.json", "email"),
    "Technicians": ("technicians.json", "email"),
    "KnowledgeBase": ("knowledge_base.json", "title"),
    "Tickets": ("tickets.json", "reference"),
}


def load_env():
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("BASEROW_")})
    return env


class Baserow:
    def __init__(self, url, token, dry_run):
        self.url = url.rstrip("/")
        self.token = token
        self.dry_run = dry_run

    def call(self, method, path, body=None):
        if self.dry_run and method != "GET":
            return {}
        req = urllib.request.Request(
            f"{self.url}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Token {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            sys.exit(f"Baserow {method} {path} failed ({err.code}): {err.read().decode()}")
        except urllib.error.URLError as err:
            sys.exit(f"Cannot reach Baserow at {self.url}: {err.reason}")

    def tables(self):
        return {t["name"]: t["id"] for t in self.call("GET", "/api/database/tables/all-tables/")}

    def rows(self, table_id):
        rows, page = [], 1
        while True:
            data = self.call("GET", f"/api/database/rows/table/{table_id}/?user_field_names=true&size=200&page={page}")
            rows += data["results"]
            if not data["next"]:
                return rows
            page += 1

    def create(self, table_id, row):
        return self.call("POST", f"/api/database/rows/table/{table_id}/?user_field_names=true", row)

    def update(self, table_id, row_id, row):
        return self.call("PATCH", f"/api/database/rows/table/{table_id}/{row_id}/?user_field_names=true", row)

    def delete(self, table_id, row_id):
        return self.call("DELETE", f"/api/database/rows/table/{table_id}/{row_id}/")


def ticket_payload(ticket, collaborator_ids, technician_ids, now):
    """Turns a seed ticket into a Baserow row (links resolved by email, due date relative to now)."""
    row = {k: v for k, v in ticket.items() if k not in ("collaborator", "technician", "due_in_hours")}
    row["collaborators"] = [collaborator_ids[ticket["collaborator"]]]
    row["technicians"] = [technician_ids[ticket["technician"]]] if ticket["technician"] else []
    row["due_at"] = (now + timedelta(hours=ticket["due_in_hours"])).isoformat()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = parser.parse_args()

    env = load_env()
    token = env.get("BASEROW_TOKEN")
    if not token:
        sys.exit("BASEROW_TOKEN is missing: create a database token in Baserow and add it to .env")
    api = Baserow(env.get("BASEROW_URL", "http://localhost:3000"), token, args.dry_run)

    table_ids = api.tables()
    missing = [name for name in TABLES if name not in table_ids]
    if missing:
        sys.exit(f"Tables not found in the database of this token: {', '.join(missing)}")

    now = datetime.now(timezone.utc)
    ids_by_key = {}
    for table, (data_file, key) in TABLES.items():
        table_id = table_ids[table]
        wanted = json.loads((DATA / data_file).read_text())
        existing = api.rows(table_id)

        # Rows Baserow creates with an empty table are noise for the demo.
        blank = [r for r in existing if not r.get(key)]
        for row in blank:
            api.delete(table_id, row["id"])
        by_key = {r[key]: r for r in existing if r.get(key)}

        created = updated = 0
        for item in wanted:
            if table == "Tickets":
                item = ticket_payload(item, ids_by_key["Collaborators"], ids_by_key["Technicians"], now)
            current = by_key.get(item[key])
            if current:
                api.update(table_id, current["id"], item)
                updated += 1
            else:
                current = api.create(table_id, item) or {"id": f"<new:{item[key]}>"}
                created += 1
            by_key[item[key]] = current

        ids_by_key[table] = {k: r["id"] for k, r in by_key.items()}
        print(f"{table:<14} created={created} updated={updated} removed_blank={len(blank)}")

    print("Dry run: nothing was written." if args.dry_run else "Seed done.")


if __name__ == "__main__":
    main()
