from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    source_system = db.Column(db.String(64))
    account_name = db.Column(db.String(64))
    direction = db.Column(db.String(16))  # 'debit' or 'credit'
    amount = db.Column(db.Float, nullable=False)
    merchant = db.Column(db.String(128))
    description = db.Column(db.String(256))
    category = db.Column(db.String(64))
    notes = db.Column(db.String(256))

    def to_dict(self):
        return {
            "date": self.date,
            "Source": self.source_system,
            "Account": self.account_name,
            "Direction": self.direction,
            "Amount": self.amount,
            "Merchant": self.merchant,
            "Description": self.description,
            "Category": self.category,
            "Notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data):
        from datetime import date as _date
        import pandas as _pd

        raw_date = data.get("Date")
        if isinstance(raw_date, str):
            d = _pd.to_datetime(raw_date).date()
        elif isinstance(raw_date, _date):
            d = raw_date
        else:
            d = _pd.to_datetime(raw_date).date()

        return cls(
            date=d,
            source_system=data.get("Source", ""),
            account_name=data.get("Account", ""),
            direction=data.get("Direction", "debit"),
            amount=float(data.get("Amount", 0.0)),
            merchant=data.get("Merchant", ""),
            description=data.get("Description", ""),
            category=data.get("Category", ""),
            notes=data.get("Notes", ""),
        )
