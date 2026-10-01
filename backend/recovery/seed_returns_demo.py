import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


ORG_ID = "org_demo_alpha"
DATASET = "synthetic-development-v1"


def build_records():
    records = []

    scenarios = [
        ("COMPLETE", "1", "1", "channel_returns_center", False),
        ("PARTIAL", "2", "1", "channel_returns_center", False),
        ("WRONG-EVENT", "1", "1", "channel_returns_center", True),
        ("WRONG-RECIPIENT", "1", "1", "seller_warehouse", False),
    ]

    for name, charged_quantity, returned_quantity, recipient, wrong_event in scenarios:
        unit_id = f"DEV-UNIT-RETURN-{name}"
        order_id = f"DEV-ORDER-RETURN-{name}"
        sku = f"DEV-SKU-RETURN-{name}"
        event_id = f"DEV-RETURN-EVENT-{name}"

        charge = {
            "org_id": ORG_ID,
            "dataset": DATASET,
            "line_id": f"DEV-FEE-RETURN-{name}",
            "report_type": "fee_report",
            "unit_id": unit_id,
            "order_id": order_id,
            "sku": sku,
            "charge_type": "refund_issued_item_not_returned",
            "quantity": charged_quantity,
            "amount_usd": "10.00",
            "currency": "USD",
            "posted_date": "2026-06-20",
            "return_event_id": event_id,
            "required_return_recipient": "channel_returns_center",
            "return_due_at": "2026-06-15T12:00:00Z",
            "evidence_description": (
                "Invented development charge and deadline. "
                "Not a real marketplace rule, fee or event."
            ),
        }

        returned = {
            "org_id": ORG_ID,
            "dataset": DATASET,
            "record_id": f"DEV-RETURN-RECORD-{name}",
            "unit_id": unit_id,
            "order_id": order_id,
            "ordered_sku": sku,
            "return_event_id": (
                f"DEV-OTHER-RETURN-EVENT-{name}"
                if wrong_event
                else event_id
            ),
            "identity_match": "yes",
            "operator_disposition": "restock",
            "processing_status": "complete",
            "receipt_status": "received",
            "received_by": recipient,
            "received_at": "2026-06-10T10:00:00Z",
            "captured_at": "2026-06-10T10:05:00Z",
            "returned_quantity": returned_quantity,
            "photo_refs": "",
            "operator_id": "synthetic-demo-operator",
            "evidence_description": (
                "Invented development receipt. "
                "No actual image or marketplace event."
            ),
        }

        records.append(("fees", charge))
        records.append(("returns", returned))

    return records


def seed():
    inserted = 0
    already_present = 0

    with connection(ORG_ID) as conn:
        for row_number, (kind, raw) in enumerate(
            build_records(), start=1
        ):
            canonical = json.dumps(
                raw,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )

            content_hash = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()

            cursor = conn.execute(
                """
                INSERT INTO recovery.records (
                    id,
                    org_id,
                    kind,
                    raw,
                    row_number,
                    content_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id, kind, content_hash)
                DO NOTHING
                """,
                (
                    uuid4(),
                    ORG_ID,
                    kind,
                    Jsonb(raw),
                    row_number,
                    content_hash,
                ),
            )

            if cursor.rowcount == 1:
                inserted += 1
            else:
                already_present += 1

    print(
        "Synthetic returns development records:",
        f"inserted={inserted},",
        f"already_present={already_present}",
    )


if __name__ == "__main__":
    seed()