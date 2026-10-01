from datetime import datetime, timezone


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp.")

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        raise ValueError("Timezone required.")

    return parsed.astimezone(timezone.utc)


def positive_integer(value):
    quantity = int(str(value))

    if quantity < 1:
        raise ValueError("Positive quantity required.")

    return quantity


def assess_return_charge(charge, evidence):
    """
    Development adapter for documented return receipt.
    Not an official evidence contract or eligibility policy.
    """
    def finish(assessment, reason, ids=None):
        return {
            "assessment": assessment,
            "claim_status": "REVIEW",
            "claim_amount_usd": None,
            "supporting_evidence_ids": ids or [],
            "flags": [],
            "reason": reason,
        }

    required = (
        "org_id",
        "unit_id",
        "order_id",
        "sku",
        "return_event_id",
        "required_return_recipient",
        "return_due_at",
    )

    if any(not charge.get(field) for field in required):
        return finish(
            "UNCERTAIN",
            "Return event, organisation, unit, order, SKU, "
            "required recipient and return deadline must "
            "be explicitly documented.",
        )

    try:
        due = timestamp(charge["return_due_at"])
        quantity = positive_integer(charge.get("quantity"))
    except (ValueError, TypeError):
        return finish(
            "UNCERTAIN",
            "Return deadline or charged quantity is invalid.",
        )

    relevant = [
        item for item in evidence
        if (
            item.get("manager") == "returns"
            and item["raw"].get("return_event_id")
            == charge["return_event_id"]
        )
    ]

    if not relevant:
        return finish(
            "SILENT",
            "No return record identifies the charged return event.",
        )

    # Do not sum multiple observations of a potentially identical
    # physical return without a defined allocation contract.
    if len(relevant) != 1:
        return finish(
            "UNCERTAIN",
            "Multiple records identify this return event. "
            "Resolve duplicates or quantity allocation before claiming.",
        )

    item = relevant[0]
    row = item["raw"]

    for charge_field, evidence_field in (
        ("org_id", "org_id"),
        ("unit_id", "unit_id"),
        ("order_id", "order_id"),
        ("sku", "ordered_sku"),
    ):
        if row.get(evidence_field) != charge[charge_field]:
            return finish(
                "UNCERTAIN",
                f"Return record does not establish matching "
                f"{charge_field}.",
            )

    if (
        row.get("identity_match") != "yes"
        or row.get("operator_disposition")
        in {None, "", "uncertain", "pending", "pending_review"}
        or row.get("processing_status")
        in {"pending", "pending_review"}
    ):
        return finish(
            "UNCERTAIN",
            "Returned-item identity or review state is insufficient.",
        )

    if (
        row.get("receipt_status") != "received"
        or row.get("received_by")
        != charge["required_return_recipient"]
    ):
        return finish(
            "UNCERTAIN",
            "Receipt by the required return recipient is not established.",
        )

    try:
        received = timestamp(row.get("received_at"))
        captured = timestamp(row.get("captured_at"))
        returned_quantity = positive_integer(
            row.get("returned_quantity")
        )
    except (ValueError, TypeError):
        return finish(
            "UNCERTAIN",
            "Return receipt time, capture time or quantity is invalid.",
        )

    if captured < received:
        return finish(
            "UNCERTAIN",
            "The record was captured before its claimed receipt time.",
        )

    if received > due:
        return finish(
            "UNCERTAIN",
            "Receipt occurred after the stated return deadline. "
            "Late-return eligibility requires policy review.",
        )

    if returned_quantity != quantity:
        return finish(
            "UNCERTAIN",
            "Returned quantity differs from the charged quantity. "
            "Partial recovery or allocation requires separate reasoning.",
        )

    record_id = item.get("record_id")

    if not record_id:
        return finish(
            "UNCERTAIN",
            "The return evidence lacks a citable record ID.",
        )

    return finish(
        "CONTRADICTS",
        f"Return record {record_id} identifies the same return event, "
        f"order, item and quantity, and reports receipt by "
        f"{charge['required_return_recipient']} before the stated "
        "deadline. This contradicts the item-not-returned allegation. "
        "Source authenticity, deadline authority and channel eligibility "
        "remain unverified.",
        [record_id],
    )