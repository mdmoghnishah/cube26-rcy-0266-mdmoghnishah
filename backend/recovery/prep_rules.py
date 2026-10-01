from datetime import datetime, timezone
from recovery.shipment_rules import assess_dispatch_packaging


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string.")

    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone.")

    return parsed.astimezone(timezone.utc)


def assess_specific_prep_charge(charge, evidence):
    """
    Development adapter for explicit allegations.

    This is not an official cross-manager evidence contract.
    It does not establish marketplace policy or approve money.
    """
    allegation = charge.get("alleged_defect")

    if allegation != "polybag_not_sealed":
        return None
    
    if charge.get("allegation_scope") == "at_dispatch":
        return assess_dispatch_packaging(charge, evidence)

    def outcome(assessment, reason, ids=None, flags=None):
        return {
            "assessment": assessment,
            "reason": reason,
            "claim_status": "REVIEW",
            "claim_amount_usd": None,
            "supporting_evidence_ids": ids or [],
            "flags": flags or [],
        }

    event_id = charge.get("inspection_event_id")

    if not event_id:
        return outcome(
            "UNCERTAIN",
            "The specific allegation is known, but its "
            "inspection event ID is missing.",
        )

    try:
        event_time = parse_timestamp(
            charge.get("event_at")
        )
    except (ValueError, TypeError):
        return outcome(
            "UNCERTAIN",
            "The allegation lacks a valid event timestamp "
            "with a timezone.",
        )

    relevant = [
        item
        for item in evidence
        if (
            item.get("manager") == "prep"
            and item["raw"].get("inspection_event_id")
            == event_id
        )
    ]

    if not relevant:
        return outcome(
            "SILENT",
            "Prep records exist, but none identifies the "
            "inspection event associated with this allegation.",
        )

    observations = []

    for item in relevant:
        row = item["raw"]

        # Require positive identifier agreement, not merely
        # the absence of a conflicting identifier.
        for field in ("sku", "fnsku", "fba_shipment_id"):
            if (
                not charge.get(field)
                or row.get(field) != charge[field]
            ):
                return outcome(
                    "UNCERTAIN",
                    f"Matching {field} is required to link "
                    "this observation to the charged item.",
                )

        try:
            captured = parse_timestamp(
                row.get("captured_at")
            )
        except (ValueError, TypeError):
            return outcome(
                "UNCERTAIN",
                "Matched prep evidence has an invalid timestamp.",
            )

        # Earlier prep can be followed by a change in condition.
        # Later prep cannot prove condition at the earlier event.
        if captured != event_time:
            return outcome(
                "UNCERTAIN",
                "The observation timestamp differs from the "
                "alleged inspection event. Condition at that "
                "event is not established.",
            )

        if (
            row.get("operator_verdict")
            in {"uncertain", "pending", "pending_review"}
            or row.get("processing_status")
            in {"pending", "pending_review"}
        ):
            return outcome(
                "UNCERTAIN",
                "The matched inspection is pending or uncertain.",
            )

        observed = row.get("polybag_present_sealed")

        if observed not in {"sealed", "not_sealed"}:
            return outcome(
                "UNCERTAIN",
                "The matched inspection does not establish "
                "whether the polybag was sealed.",
            )

        observations.append(
            (item["record_id"], observed)
        )

    states = {state for _, state in observations}
    ids = sorted({record_id for record_id, _ in observations})

    if len(states) != 1:
        return outcome(
            "UNCERTAIN",
            "Records for the same inspection event disagree "
            "about the polybag seal.",
            ids,
            ["CONFLICTING_OBSERVATIONS"],
        )

    references = ", ".join(ids)

    if states == {"sealed"}:
        return outcome(
            "CONTRADICTS",
            f"Inspection records {references} report a sealed "
            "polybag at the same identified event, contradicting "
            "the allegation that it was not sealed. Evidence "
            "authenticity, channel policy and monetary eligibility "
            "still require verification.",
            ids,
        )

    return outcome(
        "SUPPORTS",
        f"Inspection records {references} report an unsealed "
        "polybag at the same identified event, supporting that "
        "specific allegation. This does not independently "
        "validate the fee or its amount.",
        ids,
    )