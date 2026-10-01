import hashlib
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


ORG = "org_demo_alpha"


def build_records():
    scenarios = [
        ("SEALED", "sealed", "same"),
        ("UNSEALED", "not_sealed", "same"),
        ("UNCERTAIN", "uncertain", "same"),
        ("WRONG-EVENT", "sealed", "different"),
    ]

    records = []

    for index, (name, observation, event_match) in enumerate(
        scenarios, start=1
    ):
        unit_id = f"DEV-UNIT-{name}"
        event_id = f"DEV-EVENT-{name}"

        common = {
            "org_id": ORG,
            "unit_id": unit_id,
            "sku": f"DEV-SKU-{name}",
            "fnsku": f"DEV-FNSKU-{name}",
            "fba_shipment_id": f"DEV-SHIPMENT-{name}",
            "dataset": "synthetic-development-v1",
        }

        charge = {
            **common,
            "line_id": f"DEV-FEE-{name}",
            "report_type": "fee_report",
            "charge_type": "inbound_defect_fee",
            "quantity": "1",
            "amount_usd": "2.00",
            "posted_date": "2026-06-20",
            "alleged_defect": "polybag_not_sealed",
            "inspection_event_id": event_id,
            "event_at": "2026-06-01T10:00:00Z",
        }

        prep = {
            **common,
            "record_id": f"DEV-PREP-{name}",
            "inspection_event_id": (
                event_id
                if event_match == "same"
                else f"{event_id}-OTHER"
            ),
            "captured_at": "2026-06-01T10:00:00Z",
            "polybag_present_sealed": observation,
            "operator_verdict": (
                "uncertain"
                if observation == "uncertain"
                else "complete"
            ),
            "photo_refs": "",
            "evidence_description": (
                "Invented development observation. "
                "No actual image or marketplace event."
            ),
        }

        records.append(("fees", index, charge))
        records.append(("prep", index, prep))

    return records


def main():
    inserted = 0
    skipped = 0

    with connection(ORG) as conn:
        for kind, row_number, raw in build_records():
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
                    id,
                    org_id,
                    kind,
                    raw,
                    row_number,
                    content_hash
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
        f"Synthetic development records: "
        f"inserted={inserted}, already_present={skipped}"
    )


if __name__ == "__main__":
    main()