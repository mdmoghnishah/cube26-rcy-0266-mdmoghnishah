from recovery.db import connection


RELEVANT_MANAGERS = {
    "inbound_defect_fee": {"prep"},
    "lost_inbound": {"receiving", "prep"},
    "damaged_in_warehouse": {"receiving", "prep", "returns"},
    "fulfilment_fee_weight_tier": set(),
    "refund_issued_item_not_returned": {"returns"},
}


def identifier_conflicts(charge, evidence, manager):
    evidence_sku = (
        "ordered_sku" if manager == "returns" else "sku"
    )

    fields = [
        ("sku", evidence_sku),
        ("order_id", "order_id"),
        ("fnsku", "fnsku"),
        ("fba_shipment_id", "fba_shipment_id"),
    ]

    conflicts = []

    for charge_field, evidence_field in fields:
        expected = charge.get(charge_field)
        observed = evidence.get(evidence_field)

        if expected and observed and expected != observed:
            conflicts.append(charge_field)

    return conflicts


def match_charges(org_id):
    with connection(org_id) as conn:
        records = conn.execute(
            """
            SELECT id, org_id, kind, raw
            FROM recovery.records
            ORDER BY imported_at, id
            """
        ).fetchall()

    charges = [
        record for record in records
        if record["kind"] == "fees"
    ]

    evidence = [
        record for record in records
        if record["kind"] != "fees"
    ]

    results = []

    for charge_record in charges:
        charge = charge_record["raw"]
        relevant = RELEVANT_MANAGERS.get(
            charge["charge_type"], set()
        )

        matches = []

        for evidence_record in evidence:
            row = evidence_record["raw"]

            if evidence_record["org_id"] != org_id:
                continue

            if row["unit_id"] != charge["unit_id"]:
                continue

            if evidence_record["kind"] not in relevant:
                continue

            matches.append({
                "record_id": row["record_id"],
                "manager": evidence_record["kind"],
                "captured_at": row.get("captured_at"),
                "identifier_conflicts": identifier_conflicts(
                    charge, row, evidence_record["kind"]
                ),
                "raw": row,
            })

        results.append({
            "line_id": charge["line_id"],
            "org_id": org_id,
            "unit_id": charge["unit_id"],
            "charge_type": charge["charge_type"],
            "charge": charge,
            "evidence": matches,
        })

    return results


if __name__ == "__main__":
    for org in ("org_demo_alpha", "org_demo_bravo"):
        results = match_charges(org)

        print(f"\n{org}: {len(results)} report lines")

        for result in results[:5]:
            evidence_ids = [
                record["record_id"]
                for record in result["evidence"]
            ]

            print(
                result["line_id"],
                result["charge_type"],
                "→",
                evidence_ids or "No relevant evidence",
            )