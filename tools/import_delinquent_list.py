"""Import the County Collector's delinquent real-estate list (from a records request).

The Garland County Collector does not publish the list online, but it is a public
record: ask for "the current delinquent real estate tax list with parcel number,
owner, and amount due" through https://garlandcountyar.nextrequest.com/ and you get
a spreadsheet. Drop it here:

    python3 tools/import_delinquent_list.py ~/Downloads/delinquent.csv --year 2025

Accepts .csv, .xls or .xlsx. Columns are matched by name, loosely: parcel / parcel
number / parcel id; owner / name / taxpayer; amount / total / due / balance; years /
tax years. Every row that matches a property we hold gets a FACT
'tax_delinquent_county' with the amount and the list date, and tax_status
DELINQUENT. Rows for parcels we do not hold are counted, not invented.
"""
import csv, os, re, sys
from datetime import date
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q  # noqa: E402
from hunter import store  # noqa: E402
from hunter.normalize import normalize_parcel  # noqa: E402

SRC = "county_delinquent_list"
LABEL = "Garland County Collector - delinquent real estate list (public records request)"


def rows_from(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    if ext in (".xls", ".xlsx"):
        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(path); sh = wb.sheet_by_index(0)
            head = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
            return [{head[c]: sh.cell_value(r, c) for c in range(sh.ncols)} for r in range(1, sh.nrows)]
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True).active
        rows = list(ws.iter_rows(values_only=True)); head = [str(h or "").strip() for h in rows[0]]
        return [dict(zip(head, r)) for r in rows[1:]]
    raise SystemExit("give me a .csv, .xls or .xlsx")


def pick(row, *names):
    for k, v in row.items():
        kl = (k or "").lower()
        if any(n in kl for n in names) and v not in (None, ""):
            return v
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    path = sys.argv[1]
    year = sys.argv[sys.argv.index("--year") + 1] if "--year" in sys.argv else str(date.today().year - 1)
    fips = sys.argv[sys.argv.index("--county") + 1] if "--county" in sys.argv else "05051"
    init_db()
    held = {normalize_parcel(r["parcel_id"]): r["id"] for r in q("SELECT id, parcel_id FROM properties WHERE parcel_id IS NOT NULL AND county_fips=?", (fips,))}
    n_rows = matched = 0
    for row in rows_from(path):
        parcel = pick(row, "parcel")
        if not parcel:
            continue
        n_rows += 1
        pid = held.get(normalize_parcel(str(parcel)))
        if not pid:
            continue
        owner = pick(row, "owner", "name", "taxpayer") or ""
        amt = pick(row, "amount", "total", "due", "balance")
        try:
            amt = float(re.sub(r"[^0-9.]", "", str(amt))) if amt is not None else None
        except ValueError:
            amt = None
        years = pick(row, "years", "year") or year
        store.store_evidence(pid, [{
            "field": "tax_delinquent_county",
            "value": f"on the Collector's delinquent list for tax year {years}" + (f": ${amt:,.2f} due" if amt else "") + (f" (listed to {owner})" if owner else ""),
            "evidence_type": "FACT", "confidence": "HIGH", "source": SRC, "source_name": LABEL,
            "source_url": "https://garlandcountyar.nextrequest.com/", "effective_date": date.today().isoformat(),
            "raw_ref": f"[key delinq:{normalize_parcel(str(parcel))}:{years}] file {os.path.basename(path)}"}])
        if amt:
            store.store_evidence(pid, [{"field": "tax_amount_owed_county", "value": f"{amt:.2f}", "evidence_type": "FACT",
                                        "confidence": "HIGH", "source": SRC, "source_name": LABEL}])
        store.set_fields(pid, {"tax_status": "DELINQUENT"}, SRC)
        matched += 1
    print(f"{n_rows} rows in the list; {matched} matched properties we hold ({n_rows - matched} we do not track yet)")


if __name__ == "__main__":
    main()
