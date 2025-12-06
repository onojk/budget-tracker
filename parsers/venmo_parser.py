from datetime import datetime
from models import Transaction
import pandas as pd

def parse_venmo_csv_file(filepath, session):
    try:
        df = pd.read_csv(filepath, skiprows=6)  # Venmo has 6 header rows
    except Exception as e:
        print(f"    [ERROR] Could not read {filepath.name}: {e}")
        return 0

    if df.empty:
        return 0

    added = 0
    for _, row in df.iterrows():
        try:
            date_str = str(row.get("Date", "")).split()[0]
            date = datetime.strptime(date_str, "%Y-%m-%d").date()

            amount_str = str(row.get("Amount", "0")).replace("$", "").replace(",", "")
            amount = float(amount_str)

            merchant = (
                row.get("Note") or row.get("From") or row.get("To") or "Venmo Transfer"
            )

            tx = Transaction(
                date=date,
                amount=amount,  # Venmo already signs correctly (negative = you paid)
                merchant=str(merchant)[:100],
                source="Venmo",
                category="Transfer" if "payment" in str(row.get("Type", "")).lower() else "Income",
            )
            session.add(tx)
            added += 1
        except Exception as e:
            print(f"    [WARN] Bad row skipped: {e}")

    session.commit()
    return added
