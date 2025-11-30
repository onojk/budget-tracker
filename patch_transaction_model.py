#!/usr/bin/env python3
"""
Patch models.py to add:
- Transaction.is_transfer
- Transaction.linked_transaction_id
- Transaction.linked_transaction relationship

Safe behavior:
- If is_transfer already exists, it does nothing.
"""

from pathlib import Path

MODEL_PATH = Path("models.py")

def main():
    if not MODEL_PATH.exists():
        print("ERROR: models.py not found in current directory.")
        return

    text = MODEL_PATH.read_text()

    # If we've already patched, bail out
    if "is_transfer = db.Column" in text:
        print("patch_transaction_model: is_transfer already present, nothing to do.")
        return

    lines = text.splitlines()

    # Find the Transaction model class
    class_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("class Transaction("):
            class_idx = i
            break

    if class_idx is None:
        print("ERROR: Could not find 'class Transaction(' in models.py")
        return

    # Find a good insertion point inside the class:
    # we'll place our fields right after the first 'id = db.Column' we see
    insert_idx = None
    indent = "    "  # default 4 spaces for class body

    for i in range(class_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # stop if we reached the next top-level class/def
        if stripped.startswith("class ") or stripped.startswith("def "):
            break

        # remember indentation of the first field if we see one
        if "id = db.Column" in stripped:
            # Compute indentation from this line
            indent = line[: len(line) - len(line.lstrip())]
            insert_idx = i + 1
            break

    if insert_idx is None:
        print("ERROR: Could not find 'id = db.Column' inside Transaction class.")
        return

    block = f"""
{indent}# Internal transfer/mirroring flags (auto-patched)
{indent}is_transfer = db.Column(db.Boolean, default=False, nullable=False)

{indent}linked_transaction_id = db.Column(
{indent}    db.Integer,
{indent}    db.ForeignKey("transaction.id"),  # keep in sync with __tablename__
{indent}    nullable=True,
{indent})

{indent}linked_transaction = db.relationship(
{indent}    "Transaction",
{indent}    remote_side=[id],
{indent}    uselist=False,
{indent}    post_update=True,
{indent})
""".rstrip("\\n")

    new_lines = lines[:insert_idx] + [block] + lines[insert_idx:]
    MODEL_PATH.write_text("\\n".join(new_lines))

    print("patch_transaction_model: Successfully patched Transaction model in models.py")

if __name__ == "__main__":
    main()
