"""
Send emails to leads that are marked "Email found — pending approval" in the sheet.
Triggered by /leads approve in Slack.
"""
import json
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import gspread
import requests
from google.oauth2.service_account import Credentials

SCOPES        = ["https://www.googleapis.com/auth/spreadsheets"]
GMAIL_USER    = os.environ["GMAIL_USER"]
GMAIL_PASS    = os.environ["GMAIL_PASS"]
SHEET_ID      = os.environ["SHEET_ID"]
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")

COL_NAME   = 2
COL_PHONE  = 3
COL_QUERY  = 1
COL_SCORE  = 9
COL_EMAIL  = 10
COL_STATUS = 11

HIGH_VALUE_TRADES = [
    "electrician", "plumber", "plumbing", "hvac", "roofing", "roofer",
    "contractor", "landscaping", "landscaper", "pool", "pest control",
    "painting", "painter", "handyman", "auto repair", "cleaning",
]


def get_trade(row):
    query = str(row[COL_QUERY]).lower() if len(row) > COL_QUERY else ""
    for trade in HIGH_VALUE_TRADES:
        if trade in query:
            return trade.title()
    return "local business"


def get_city(row):
    query = str(row[COL_QUERY]) if len(row) > COL_QUERY else ""
    parts = query.strip().split()
    return " ".join(parts[-2:]) if len(parts) >= 2 else "your area"


def build_email(name, trade, city):
    subject = f"Quick question about {name}'s online presence"
    body = f"""Hi {name},

I was searching for {trade.lower()}s in {city} on Google Maps and came across your business. I noticed you don't have a website yet — I think that's a big opportunity.

My name is Ryan, and I run XV Connects. We build clean, professional websites specifically for local businesses like yours. A lot of our clients say their website became their #1 source of new customers within the first few months.

Here's what we offer:
  • Custom website built for your business — starting at $500 one-time
  • Or fully managed hosting for just $200 setup + $20/month
  • Fast turnaround — most sites go live in 7–14 days
  • Mobile-first, SEO-optimized, and built to convert visitors into calls

No lock-in contracts. You own everything.

Would you be open to a quick 10-minute call this week to see if it's a good fit?

Best,
Ryan Krauss
XV Connects
xvconnects@gmail.com
"""
    return subject, body


def send_email(to_email, subject, body):
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


def slack_notify(text):
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=5)
        except Exception:
            pass


def main():
    creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds  = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(SHEET_ID).sheet1

    all_rows = sheet.get_all_values()
    if len(all_rows) <= 1:
        print("No leads in sheet.")
        return

    rows = all_rows[1:]
    sent = 0
    failed = 0

    for i, row in enumerate(rows):
        while len(row) < COL_STATUS + 1:
            row.append("")

        status = str(row[COL_STATUS]).strip().lower()
        if "pending approval" not in status:
            continue

        name  = str(row[COL_NAME]).strip()
        email = str(row[COL_EMAIL]).strip() if len(row) > COL_EMAIL else ""
        trade = get_trade(row)
        city  = get_city(row)

        if not email:
            continue

        print(f"  Sending to {name} <{email}>...")
        subject, body = build_email(name, trade, city)
        success = send_email(email, subject, body)

        sheet_row = i + 2
        if success:
            sheet.update_cell(sheet_row, COL_STATUS + 1,
                f"Email sent — {datetime.now().strftime('%Y-%m-%d')}")
            sent += 1
            print(f"    ✓ Sent")
        else:
            sheet.update_cell(sheet_row, COL_STATUS + 1, "Send failed")
            failed += 1

        time.sleep(2)  # avoid Gmail rate limits

    msg = f":mailbox_with_mail: *Emails sent: {sent}*"
    if failed:
        msg += f" ({failed} failed)"
    msg += "\nSheet updated with send status. I'll notify you if anyone replies."
    slack_notify(msg)
    print(f"\n✓ Sent {sent}, failed {failed}")


if __name__ == "__main__":
    main()
