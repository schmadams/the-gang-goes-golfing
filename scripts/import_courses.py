# target path: scripts/import_courses.py
"""
One-off script to bulk-import UK golf clubs from the free bthree.uk Golf
Data API into our own `courses` table, so the home-course dropdown can
search them without hitting an external API on every page load.

Run once after applying courses_migration.sql:

    uv run python scripts/import_courses.py

Safe to re-run: rows are upserted on external_id, so existing clubs get
refreshed instead of duplicated.
"""
import requests

from backend.database import supabase

BASE_URL = "https://api.bthree.uk/golf/v1/clubs"
PAGE_SIZE = 100


def main():
    offset = 0
    imported = 0
    total = None

    while total is None or offset < total:
        response = requests.get(BASE_URL, params={"limit": PAGE_SIZE, "offset": offset})

        if response.status_code != 200:
            print(f"Error {response.status_code}: {response.text}")
            return

        payload = response.json()
        items = payload.get("items", [])
        total = payload.get("total", 0)

        if not items:
            break

        rows = [
            {
                "external_id": item["id"],
                "name": item["name"],
                "address1": item.get("address1"),
                "address2": item.get("address2"),
                "address3": item.get("address3"),
                "postcode": item.get("postcode"),
            }
            for item in items
        ]

        supabase.table("courses").upsert(rows, on_conflict="external_id").execute()

        imported += len(rows)
        offset += PAGE_SIZE
        print(f"Imported {imported}/{total} courses...")

    print(f"Done. {imported} courses imported.")


if __name__ == "__main__":
    main()