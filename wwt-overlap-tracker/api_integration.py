import os

SMARTSHEET_TOKEN = os.environ.get("SMARTSHEET_TOKEN", "")
SMARTSHEET_SHEET_ID = os.environ.get("SMARTSHEET_SHEET_ID", "")

COLUMN_RENAMES = {
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
    "Notes": "notes",
}


def _clean(val, default=""):
    if val is None or str(val).strip().lower() in ("nan", "none"):
        return default
    return str(val).strip()


def get_smartsheet_data(token=SMARTSHEET_TOKEN, sheet_id=SMARTSHEET_SHEET_ID):
    import smartsheet

    client = smartsheet.Smartsheet(token)
    client.errors_as_exceptions(True)

    sheet = client.Sheets.get_sheet(sheet_id)
    print(f"  Connected to sheet: {sheet.name}")

    rows = []
    for row in sheet.rows:
        row_dict = {}
        for cell in row.cells:
            col_title = next(
                (c.title for c in sheet.columns if c.id == cell.column_id), None
            )
            if col_title:
                new_key = COLUMN_RENAMES.get(col_title, col_title)
                row_dict[new_key] = cell.value
        rows.append(row_dict)

    print(f"  Loaded {len(rows)} rows from Smartsheet")
    return rows


def load_data_from_smartsheet():
    rows = get_smartsheet_data()
    cleaned = []
    for row in rows:
        cleaned.append({
            "ref_id": _clean(row.get("ref_id"), ""),
            "bsa_owner": _clean(row.get("bsa_owner"), "Unassigned"),
            "requestor": _clean(row.get("requestor"), "Unknown"),
            "system": _clean(row.get("system"), "Unknown"),
            "status": _clean(row.get("status"), "Unknown"),
            "initiative_name": _clean(row.get("initiative_name"), ""),
            "request_title": _clean(row.get("request_title"), ""),
            "priority": _clean(row.get("priority"), "Unknown"),
            "program": _clean(row.get("program"), "Unknown"),
            "request_date": _clean(row.get("request_date"), ""),
            "notes": _clean(row.get("notes"), ""),
            "request_type": _clean(row.get("request_type"), "Unknown"),
        })
    return cleaned


if __name__ == "__main__":
    print("Testing Smartsheet connection...")
    rows = load_data_from_smartsheet()
    print(f"\nSuccess! Loaded {len(rows)} rows.")
    if rows:
        print(f"Sample row: {rows[0]}")