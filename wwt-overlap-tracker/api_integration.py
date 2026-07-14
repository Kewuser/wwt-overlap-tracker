"""
Smartsheet API Integration
===========================
Pulls live project data from the Smartsheet intake form and returns
a pandas DataFrame that overlap_detector.py can use directly.

HOW TO USE:
  Day 1:  Replace SMARTSHEET_TOKEN and SMARTSHEET_SHEET_ID below with your real values.
          Run this file to test: python api_integration.py
          You should see: Loaded 144 rows from Smartsheet

  Day 5:  Move your token out of this file and into environment variables.
          The code already reads from os.environ first, so no other change needed.

HOW TO GET YOUR SMARTSHEET TOKEN:
  Smartsheet > Account (top right) > Apps & Integrations > API Access > Generate new access token

HOW TO GET YOUR SHEET ID:
  Open your Smartsheet intake form in the browser.
  The Sheet ID is the long number in the URL:
  https://app.smartsheet.com/sheets/XXXXXXXXXXXXXXXXXX  <-- that number

NOTE: Your Groq API key is NOT in this file. It goes in server.py (Day 2).

DEPENDENCIES:
  pip install smartsheet-python-sdk pandas
"""

import os
import pandas as pd

# ─── CREDENTIALS ─────────────────────────────────────────────────────────────
# Day 1: paste your real values here
# Day 5: delete the hardcoded values and rely on environment variables only
SMARTSHEET_TOKEN = os.environ.get("SMARTSHEET_TOKEN", "")
SMARTSHEET_SHEET_ID = os.environ.get("SMARTSHEET_SHEET_ID", "")
# ─────────────────────────────────────────────────────────────────────────────


def get_smartsheet_data(token=SMARTSHEET_TOKEN, sheet_id=SMARTSHEET_SHEET_ID):
    """
    Pull all rows from the Smartsheet intake form via API.
    Returns a pandas DataFrame matching the structure expected by overlap_detector.py.

    Smartsheet API docs: https://smartsheet.com/developers/api-documentation
    """
    import smartsheet  # pip install smartsheet-python-sdk

    client = smartsheet.Smartsheet(token)
    client.errors_as_exceptions(True)

    # Fetch the sheet
    sheet = client.Sheets.get_sheet(sheet_id)
    print(f"  Connected to sheet: {sheet.name}")

    # Convert rows to dicts using column titles as keys
    rows = []
    for row in sheet.rows:
        row_dict = {}
        for cell in row.cells:
            col_title = next(
                (c.title for c in sheet.columns if c.id == cell.column_id), None
            )
            if col_title:
                row_dict[col_title] = cell.value
        rows.append(row_dict)

    df = pd.DataFrame(rows)

    # Rename columns to match what overlap_detector.py expects
    # IMPORTANT: if your Smartsheet column names are different, update these
    column_renames = {
        "Ref ID": "ref_id",
        "Initiative Name": "initiative_name",
        "Request Title": "request_title",
        "Owner": "bsa_owner",
        "Requestor Name": "requestor",
        "Primary System Impacted": "system",
        "Status": "status",
        "Request Type": "request_type",
        "Request Date": "request_date",
        "Business Priority": "priority",
        "Program": "program",
    }
    df = df.rename(columns=column_renames)

    print(f"  Loaded {len(df)} rows from Smartsheet")
    return df


def load_data_from_smartsheet():
    """
    Drop-in replacement for overlap_detector.load_data().
    Pulls live data from Smartsheet instead of a local Excel file.

    HOW TO USE in overlap_detector.py (swap on Day 2):
        # Remove this line:
        #   df = load_data(EXCEL_PATH)
        # Add this instead:
        #   from api_integration import load_data_from_smartsheet
        #   df = load_data_from_smartsheet()
    """
    df = get_smartsheet_data()

    # Fill nulls the same way overlap_detector.py does
    df["ref_id"] = df.get("ref_id", pd.Series()).fillna("").astype(str).str.strip()
    df["bsa_owner"] = df.get("bsa_owner", pd.Series()).fillna("Unassigned").astype(str).str.strip()
    df["requestor"] = df.get("requestor", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["system"] = df.get("system", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["status"] = df.get("status", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["initiative_name"] = df.get("initiative_name", pd.Series()).fillna("").astype(str).str.strip()
    df["request_title"] = df.get("request_title", pd.Series()).fillna("").astype(str).str.strip()
    df["priority"] = df.get("priority", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["program"] = df.get("program", pd.Series()).fillna("Unknown").astype(str).str.strip()

    return df


if __name__ == "__main__":
    print("Testing Smartsheet connection...")
    df = load_data_from_smartsheet()
    print(f"\nSuccess! Columns: {list(df.columns)}")
    print(f"Sample row:\n{df.iloc[0].to_dict() if len(df) > 0 else 'No rows found'}")
