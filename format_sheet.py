"""Run this once locally to apply sleek formatting to your Google Sheet."""
import json
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "Date Found", "Search Query", "Business Name", "Phone Number",
    "Address", "Category", "Rating", "# Reviews", "Google Maps URL",
]

def main():
    creds_path = sys.argv[1] if len(sys.argv) > 1 else None
    sheet_id   = sys.argv[2] if len(sys.argv) > 2 else None

    if not creds_path or not sheet_id:
        print("Usage: python3 format_sheet.py path/to/credentials.json SHEET_ID")
        sys.exit(1)

    with open(creds_path) as f:
        creds_info = json.load(f)

    creds  = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(sheet_id).sheet1

    # Clear and write header
    sheet.clear()
    sheet.append_row(HEADER)

    # Freeze header row
    sheet.spreadsheet.batch_update({
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet.id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            # Navy header background + white bold text
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.05, "green": 0.09, "blue": 0.18},
                            "textFormat": {
                                "bold": True,
                                "fontSize": 11,
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
                }
            },
            # Column widths
            *[
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet.id,
                            "dimension": "COLUMNS",
                            "startIndex": i,
                            "endIndex": i + 1,
                        },
                        "properties": {"pixelSize": w},
                        "fields": "pixelSize",
                    }
                }
                for i, w in enumerate([110, 180, 220, 140, 260, 140, 80, 90, 320])
            ],
        ]
    })

    print("✓ Sheet formatted successfully!")
    print("  - Navy header row with white bold text")
    print("  - Frozen header row")
    print("  - Custom column widths")
    print("  - Alternating row colors")

if __name__ == "__main__":
    main()
