"""Read scraper CSV, filter for no-website leads, append to a Google Sheet."""
import csv
import json
import os
import sys
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from gspread_formatting import (
    CellFormat, Color, TextFormat, format_cell_range,
    set_frozen, set_column_width, batch_updater,
)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "Date Found", "Search Query", "Business Name", "Phone Number",
    "Address", "Category", "Rating", "# Reviews", "Google Maps URL",
]


def apply_formatting(sheet) -> None:
    # Dark navy header row
    header_fmt = CellFormat(
        backgroundColor=Color(0.05, 0.09, 0.18),
        textFormat=TextFormat(
            bold=True,
            foregroundColor=Color(1, 1, 1),
            fontSize=11,
            fontFamily="Inter",
        ),
        horizontalAlignment="CENTER",
        verticalAlignment="MIDDLE",
    )
    format_cell_range(sheet, "A1:I1", header_fmt)

    # Freeze header row
    set_frozen(sheet, rows=1)

    # Column widths (pixels): Date, Query, Name, Phone, Address, Category, Rating, Reviews, URL
    widths = [110, 180, 220, 140, 260, 140, 80, 90, 280]
    cols = "ABCDEFGHI"
    with batch_updater(sheet.spreadsheet) as b:
        for col, width in zip(cols, widths):
            b.set_column_width(sheet, col, width)


def main(csv_path: str) -> None:
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    sheet_id = os.environ["SHEET_ID"]

    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(sheet_id).sheet1

    # Clear sheet and write fresh header every weekly run
    sheet.clear()
    sheet.append_row(HEADER)

    existing_names = set()

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

    # Write counts to GitHub Actions output for Slack notification
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"new_leads={len(new_rows)}\n")
            f.write(f"skipped_has_site={skipped_has_site}\n")
            f.write(f"skipped_dup={skipped_dup}\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results.csv")
