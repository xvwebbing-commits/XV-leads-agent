"""
Full email pipeline:
1. Score all leads in the sheet
2. Find emails for top leads via Hunter.io
3. Slack Ryan for approval
4. On /leads approve — send emails via Gmail
"""
import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import gspread
import requests
from google.oauth2.service_account import Credentials

SCOPES       = ["https://www.googleapis.com/auth/spreadsheets"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_PASS"]
SHEET_ID     = os.environ["SHEET_ID"]
HUNTER_KEY   = os.environ["HUNTER_API_KEY"]
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")

TOP_N = 10
CONTACTED_TAB = "Contacted History"

# Column indexes (0-based)
COL_DATE     = 0
COL_QUERY    = 1
COL_NAME     = 2
COL_PHONE    = 3
COL_ADDRESS  = 4
COL_CATEGORY = 5
COL_RATING   = 6
COL_REVIEWS  = 7
COL_URL      = 8
COL_SCORE    = 9
COL_EMAIL    = 10
COL_STATUS   = 11

HIGH_VALUE_TRADES = [
    "electrician", "plumber", "plumbing", "hvac", "roofing", "roofer",
    "contractor", "landscaping", "landscaper", "pool", "pest control",
    "painting", "painter", "handyman", "auto repair", "cleaning",
]


def score_lead(row: list) -> int:
    score = 0
    try:
        reviews = int(str(row[COL_REVIEWS]).replace(",", "").strip())
        if reviews >= 50:   score += 35
        elif reviews >= 20: score += 25
        elif reviews >= 5:  score += 15
        elif reviews >= 1:  score += 5
    except (ValueError, IndexError):
        pass

    try:
        rating = float(str(row[COL_RATING]).strip())
        if rating >= 4.5:   score += 30
        elif rating >= 4.0: score += 20
        elif rating >= 3.5: score += 10
    except (ValueError, IndexError):
        pass

    query = str(row[COL_QUERY]).lower() if len(row) > COL_QUERY else ""
    cat   = str(row[COL_CATEGORY]).lower() if len(row) > COL_CATEGORY else ""
    if any(t in query + " " + cat for t in HIGH_VALUE_TRADES):
        score += 25

    if len(row) > COL_PHONE and str(row[COL_PHONE]).strip():
        score += 10

    return min(score, 100)


def get_trade(row: list) -> str:
    query = str(row[COL_QUERY]).lower() if len(row) > COL_QUERY else ""
    for trade in HIGH_VALUE_TRADES:
        if trade in query:
            return trade.title()
    return "local business"


def get_city(row: list) -> str:
    query = str(row[COL_QUERY]) if len(row) > COL_QUERY else ""
    parts = query.strip().split()
    return " ".join(parts[-2:]) if len(parts) >= 2 else "your area"


def extract_domain_from_maps_url(url: str) -> str | None:
    """Try to pull a business domain from their Maps listing URL or name search."""
    return None  # placeholder — Hunter uses company name + location instead


def find_email_hunter(business_name: str, city: str) -> str:
    """Search Hunter.io for a business email by name."""
    try:
        # Hunter domain search by company name
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={
                "company": business_name,
                "api_key": HUNTER_KEY,
                "limit": 1,
            },
            timeout=10,
        )
        data = resp.json()
        emails = data.get("data", {}).get("emails", [])
        if emails:
            return emails[0].get("value", "")

        # Fallback: email finder by first/last name guess
        resp2 = requests.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "company": business_name,
                "first_name": "info",
                "last_name": "",
                "api_key": HUNTER_KEY,
            },
            timeout=10,
        )
        data2 = resp2.json()
        return data2.get("data", {}).get("email", "")
    except Exception as e:
        print(f"  Hunter error for {business_name}: {e}")
        return ""


def build_email_body(name: str, trade: str, city: str) -> tuple[str, str]:
    subject = f"Quick question about {name}'s online presence"
    body = f"""Hi {name},

I was searching for {trade.lower()}s in {city} on Google Maps and came across your business. I noticed you don't have a website yet — I think that's a big opportunity.

My name is Ryan, and I run XV Connects. We build clean, professional websites specifically for local businesses like yours. A lot of our clients say their website became their #1 source of new customers within the first few months.

Here's what we offer:
  • Custom website built specifically for your business
  • Fast turnaround — most sites go live in 7–14 days
  • Built to turn website visitors into phone calls

No lock-in contracts. You own everything.

Would you be open to a quick 10-minute call this week to see if it's a good fit?

Best,
Ryan Krauss
XV Connects
xvconnects@gmail.com
"""
    return subject, body


def send_email(to_email: str, subject: str, body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Ryan @ XV Connects <{GMAIL_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"  Send failed to {to_email}: {e}")
        return False


def get_contacted_set(spreadsheet) -> set:
    """Return a set of lowercased business names already contacted."""
    try:
        tab = spreadsheet.worksheet(CONTACTED_TAB)
    except gspread.WorksheetNotFound:
        tab = spreadsheet.add_worksheet(title=CONTACTED_TAB, rows=1000, cols=4)
        tab.append_row(["Date Contacted", "Business Name", "Email", "Phone"])
        return set()
    rows = tab.get_all_values()
    return {r[1].strip().lower() for r in rows[1:] if len(r) > 1 and r[1].strip()}


def slack_notify(text: str):
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=5)
        except Exception:
            pass


def main():
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds  = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    sheet  = spreadsheet.sheet1

    contacted = get_contacted_set(spreadsheet)
    print(f"Loaded {len(contacted)} previously-contacted businesses.")

    all_rows = sheet.get_all_values()
    if len(all_rows) <= 1:
        print("No leads in sheet.")
        return

    rows = all_rows[1:]

    # Ensure header has Score, Email, Status columns
    header = all_rows[0]
    if len(header) <= COL_SCORE:
        sheet.update_cell(1, COL_SCORE  + 1, "Score")
        sheet.update_cell(1, COL_EMAIL  + 1, "Email Found")
        sheet.update_cell(1, COL_STATUS + 1, "Email Status")

    # Score all leads
    scored = []
    for i, row in enumerate(rows):
        while len(row) < COL_STATUS + 1:
            row.append("")
        score = score_lead(row)
        scored.append((i + 2, score, row))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Skip businesses already contacted in prior runs
    fresh = [
        s for s in scored
        if str(s[2][COL_NAME]).strip().lower() not in contacted
    ]
    skipped = len(scored) - len(fresh)
    top = fresh[:TOP_N]

    print(f"Scored {len(scored)} leads ({skipped} already contacted). Finding emails for top {len(top)}...")

    # Mark all top picks as seen immediately so they don't repeat next run
    try:
        contacted_tab = spreadsheet.worksheet(CONTACTED_TAB)
    except gspread.WorksheetNotFound:
        contacted_tab = spreadsheet.add_worksheet(title=CONTACTED_TAB, rows=1000, cols=4)
        contacted_tab.append_row(["Date Contacted", "Business Name", "Email", "Phone"])

    today = datetime.now().strftime('%Y-%m-%d')
    for _, _, row in top:
        name_val = str(row[COL_NAME]).strip()
        if name_val.lower() not in contacted:
            phone_val = str(row[COL_PHONE]).strip() if len(row) > COL_PHONE else ""
            contacted_tab.append_row([today, name_val, "", phone_val])
            contacted.add(name_val.lower())

    found_leads = []
    for sheet_row, score, row in top:
        name  = str(row[COL_NAME]).strip()
        city  = get_city(row)
        trade = get_trade(row)
        phone = str(row[COL_PHONE]).strip()

        # Update score
        sheet.update_cell(sheet_row, COL_SCORE + 1, score)

        # Skip if already emailed
        status = str(row[COL_STATUS]).strip()
        if "sent" in status.lower():
            print(f"  Skipping {name} — already emailed")
            continue

        # Find email
        print(f"  [{score}/100] {name} — searching email...")
        email = find_email_hunter(name, city)
        time.sleep(1)  # be polite to Hunter API

        if email:
            sheet.update_cell(sheet_row, COL_EMAIL + 1, email)
            sheet.update_cell(sheet_row, COL_STATUS + 1, "Email found — pending approval")
            found_leads.append({
                "sheet_row": sheet_row,
                "name": name,
                "email": email,
                "phone": phone,
                "trade": trade,
                "city": city,
                "score": score,
            })
            print(f"    ✓ Found: {email}")
        else:
            sheet.update_cell(sheet_row, COL_STATUS + 1, "No email found")
            print(f"    — No email found")

    # Slack summary asking for approval
    if found_leads:
        lines = [f":email: *Weekly leads ready — {len(found_leads)} emails found. Reply `/leads approve` to send or `/leads skip` to cancel.*\n"]
        for l in found_leads:
            subject, body = build_email_body(l['name'], l['trade'], l['city'])
        preview = body.split('\n')[2][:120]  # first real line of body
        lines.append(f"  • *{l['name']}* | {l['email']} | Score: {l['score']}/100\n    _{preview}..._")
        slack_notify("\n".join(lines))
        print(f"\n✓ Slacked approval request for {len(found_leads)} leads")
    else:
        slack_notify(":mag: Weekly scrape complete — no emails found this week. Check sheet for phone leads.")
        print("No emails found this week.")


if __name__ == "__main__":
    main()
