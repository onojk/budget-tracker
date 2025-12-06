from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Transaction(db.Model):
    __tablename__ = "transaction"

    id = db.Column(db.Integer, primary_key=True)
    # Optional: which OCR file this came from
    file_checksum = db.Column(db.String(64), index=True, nullable=True)
    source_filename = db.Column(db.String(255), nullable=True)

    # Internal transfer/mirroring flags
    is_transfer = db.Column(db.Boolean, default=False, nullable=False)

    linked_transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("transaction.id"),  # keep in sync with __tablename__
        nullable=True,
    )

    linked_transaction = db.relationship(
        "Transaction",
        remote_side=[id],
        uselist=False,
        post_update=True,
    )

    # Core transaction fields
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
        """
        Build a Transaction from a dict, using the same keys your importer expects.
        """
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


class CategoryRule(db.Model):
    __tablename__ = "category_rules"

    id = db.Column(db.Integer, primary_key=True)
    # Optional: which OCR file this came from
    file_checksum = db.Column(db.String(64), index=True, nullable=True)
    source_filename = db.Column(db.String(255), nullable=True)

    # What we match on
    merchant = db.Column(db.String(255), index=True, nullable=True)
    account_name = db.Column(db.String(255), index=True, nullable=True)
    method = db.Column(db.String(64), index=True, nullable=True)

    # What we apply
    category = db.Column(db.String(255), nullable=False)

    # For debugging/analytics
    use_count = db.Column(db.Integer, default=0)



class OcrRejectedLine(db.Model):
    __tablename__ = "ocr_rejected_lines"

    id = db.Column(db.Integer, primary_key=True)

    file_name = db.Column(db.String(255), nullable=False)
    line_num = db.Column(db.Integer, nullable=True)

    reason = db.Column(db.String(255), nullable=False)

    amount = db.Column(db.Numeric(12, 2), nullable=True)
    raw_text = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<OcrRejectedLine {self.file_name}:{self.line_num} {self.reason}>"
