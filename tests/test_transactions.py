"""
Tests for the delete-transaction endpoint.
"""
from models import db, Transaction


def test_delete_transaction_success(client, make_transaction, app):
    tx_id = make_transaction(merchant="To Be Deleted", amount=-99.00)

    resp = client.delete(f"/api/transactions/{tx_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["deleted"] is True
    assert body["id"] == tx_id

    # Confirm the row is gone from the database.
    tx = db.session.get(Transaction, tx_id)
    assert tx is None


def test_delete_transaction_404(client):
    resp = client.delete("/api/transactions/999999")

    assert resp.status_code == 404
    body = resp.get_json()
    assert body == {"error": "not found"}


def test_delete_unlinks_transfer_pair(client, make_transaction, app):
    """
    When one half of a transfer pair is deleted, the surviving partner's
    linked_transaction_id must be nulled out rather than left as a dangling FK.
    """
    id_a = make_transaction(merchant="Transfer Out", amount=-500.00)
    id_b = make_transaction(merchant="Transfer In",  amount=500.00)

    # Link the two transactions to form a transfer pair.
    tx_a = db.session.get(Transaction, id_a)
    tx_b = db.session.get(Transaction, id_b)
    tx_a.linked_transaction_id = id_b
    tx_b.linked_transaction_id = id_a
    db.session.commit()

    # Delete transaction A.
    resp = client.delete(f"/api/transactions/{id_a}")
    assert resp.status_code == 200

    # B must still exist…
    tx_b = db.session.get(Transaction, id_b)
    assert tx_b is not None, "partner transaction should survive deletion of its pair"

    # …with its back-reference cleared.
    assert tx_b.linked_transaction_id is None, (
        "partner's linked_transaction_id should be None after its pair is deleted"
    )
