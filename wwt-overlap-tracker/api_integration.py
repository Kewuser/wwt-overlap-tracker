import os
import pandas as pd

SMARTSHEET_TOKEN = os.environ.get("SMARTSHEET_TOKEN", "")
SMARTSHEET_SHEET_ID = os.environ.get("SMARTSHEET_SHEET_ID", "")


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
                row_dict[col_title] = cell.value
        rows.append(row_dict)

    df = pd.DataFrame(rows)

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
    df = get_smartsheet_data()

    df["ref_id"] = df.get("ref_id", pd.Series()).fillna("").astype(str).str.strip()
    df["bsa_owner"] = df.get("bsa_owner", pd.Series()).fillna("Unassigned").astype(str).str.strip()
    df["requestor"] = df.get("requestor", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["system"] = df.get("system", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["status"] = df.get("status", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["initiative_name"] = df.get("initiative_name", pd.Series()).fillna("").astype(str).str.strip()
    df["request_title"] = df.get("request_title", pd.Series()).fillna("").astype(str).str.strip()
    df["priority"] = df.get("priority", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["program"] = df.get("program", pd.Series()).fillna("Unknown").astype(str).str.strip()
    df["request_date"] = df.get("request_date", pd.Series()).fillna("").astype(str).str.strip()

    return df


if __name__ == "__main__":
    print("Testing Smartsheet connection...")
    df = load_data_from_smartsheet()
    print(f"\nSuccess! Columns: {list(df.columns)}")
    print(f"Sample row:\n{df.iloc[0].to_dict() if len(df) > 0 else 'No rows found'}")