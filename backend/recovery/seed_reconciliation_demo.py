import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


ORG = "org_demo_alpha"


def build_records():
    records = []

    for name, reimbursement_amount in (
        ("PARTIAL", "4.00"),
        ("FULL", "10.00"),
        ("DUPLICATE", None),
    ):
        common = {
            "org_id": ORG,
            "unit_id": f"DEV-UNIT-RECON-{name}",
            "sku": f"DEV-SKU-RECON-{name}",
            "fnsku": f"DEV-FNSKU-RECON-{name}",
            "fba_shipment_id": f"DEV-SHIPMENT-RECON-{name}",
            "dataset": "synthetic-development-v1",
        }

        event_id = f"DEV-EVENT-RECON-{name}"

        charge = {
            **common,
            "line_id": f"DEV-FEE-RECON-{name}",
            "report_type": "fee_report",
            "charge_type": "inbound_defect_fee",
            "quantity": "1",
            "amount_usd": "10.00",
            "currency": "USD",
            "posted_date": "2026-06-20",
            "source_transaction_id": f"DEV-TX-CHARGE-{name}",
            "alleged_defect": "polybag_not_sealed",
            "inspection_event_id": event_id,
            "event_at": "2026-06-01T10:00:00Z",
        }

        prep = {
            **common,
            "record_id": f"DEV-PREP-RECON-{name}",
            "inspection_event_id": event_id,
            "captured_at": "2026-06-01T10:00:00Z",
            "polybag_present_sealed": "sealed",
            "operator_verdict": "complete",
            "photo_refs": "",
            "evidence_description": (
                "Invented development inspection. "
                "No actual image or marketplace event."
            ),
        }

        records.extend([
            ("fees", charge),
            ("prep", prep),
        ])

        if reimbursement_amount is not None:
            payment = {
                **common,
                "line_id": f"DEV-REIM-RECON-{name}",
                "report_type": "reimbursement_report",
                "charge_type": "inbound_defect_fee",
                "quantity": "1",
                "amount_usd": reimbursement_amount,
                "currency": "USD",
                "posted_date": "2026-06-25",
                "source_transaction_id": f"DEV-TX-PAYMENT-{name}",
                "applies_to_line_id": charge["line_id"],
            }
            records.append(("fees", payment))

        else:
            duplicate = deepcopy(charge)
            duplicate["line_id"] = "DEV-FEE-RECON-DUPLICATE-COPY"
            # Same financial transaction, different report line.
            records.append(("fees", duplicate))

    return records


def main():
    inserted = 0
    skipped = 0

    with connection(ORG) as conn:
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

            saved = conn.execute(
                """
                INSERT INTO recovery.records (
                    id, org_id, kind, raw,
                    row_number, content_hash
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (
                    org_id, kind, content_hash
                ) DO NOTHING
                RETURNING id
                """,
                (
                    uuid4(),
                    ORG,
                    kind,
                    Jsonb(raw),
                    row_number,
                    content_hash,
                ),
            ).fetchone()

            if saved:
                inserted += 1
            else:
                skipped += 1

    print(
        f"Reconciliation demo: inserted={inserted}, "
        f"already_present={skipped}"
    )


if __name__ == "__main__":
    main()