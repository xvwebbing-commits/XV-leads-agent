"""Read scraper CSV, filter for no-website leads, append to a Google Sheet."""
import csv
import json
import os
import sys
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "date_found", "query", "name", "phone", "address",
    "category", "rating", "reviews", "maps_url",
]


def main(csv_path: str) -> None:
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    sheet_id = os.environ["SHEET_ID"]

    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(sheet_id).sheet1

    # Add header row once, if sheet is empty
    existing = sheet.get_all_values()
    if not existing:
        sheet.append_row(HEADER)

    # Track existing names to dedupe across nightly runs
    existing_names = {row[2].strip().lower() for row in existing[1:] if len(row) >= 3}

    today = datetime.now().strftime("%Y-%m-%d")
    new_rows = []
    skipped_has_site = 0
    skipped_dup = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            website = (row.get("website") or "").strip()
            if website:
                skipped_has_site += 1
                continue

            name = (row.get("title") or "").strip()
            if not name:
                continue
            if name.lower() in existing_names:
                skipped_dup += 1
                continue
            existing_names.add(name.lower())

            new_rows.append([
                today,
                row.get("input_id", ""),
                name,
                row.get("phone", ""),
                row.get("address", ""),
                row.get("category", ""),
                row.get("review_rating", ""),
                row.get("review_count", ""),
                row.get("link", ""),
            ])

    if new_rows:
        sheet.append_rows(new_rows, value_input_option="RAW")

    print(f"✓ Appended {len(new_rows)} new no-website leads")
    print(f"  Skipped (has website): {skipped_has_site}")
    print(f"  Skipped (duplicate):   {skipped_dup}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results.csv")
