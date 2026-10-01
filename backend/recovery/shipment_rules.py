from datetime import datetime, timezone


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp.")

    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        raise ValueError("Missing timezone.")

    return parsed.astimezone(timezone.utc)


def assess_dispatch_packaging(charge, evidence):
    """
    Development adapter for a specific allegation about
    packaging condition at dispatch.

    Source assertions are evaluated, not authenticated.
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

    if charge.get("allegation_scope") != "at_dispatch":
        return finish(
            "UNCERTAIN",
            "The allegation does not establish which stage "
            "of the shipment the packaging defect concerns.",
        )

    record_id = charge.get("final_prep_record_id")

    if not record_id:
        return finish(
            "UNCERTAIN",
            "No final-prep record is explicitly linked "
            "to this dispatch allegation.",
        )

    relevant = [
        item for item in evidence
        if (
            item.get("manager") == "prep"
            and item.get("record_id") == record_id
        )
    ]

    if not relevant:
        return finish(
            "SILENT",
            "The referenced final-prep record is unavailable.",
        )

    if len(relevant) != 1:
        return finish(
            "UNCERTAIN",
            "The final-prep record reference is not unique.",
        )

    item = relevant[0]
    row = item["raw"]

    for field in ("org_id", "unit_id", "sku", "fnsku",
                  "fba_shipment_id"):
        if (
            not charge.get(field)
            or row.get(field) != charge[field]
        ):
            return finish(
                "UNCERTAIN",
                f"Matching {field} is required to associate "
                "the final inspection with this shipment.",
            )

    try:
        captured = timestamp(row.get("captured_at"))
        dispatched = timestamp(charge.get("dispatched_at"))
        posted = datetime.fromisoformat(
            charge["posted_date"]
        ).date()
    except (ValueError, TypeError, KeyError):
        return finish(
            "UNCERTAIN",
            "Inspection, dispatch or posting time is invalid.",
        )

    if captured > dispatched:
        return finish(
            "UNCERTAIN",
            "The inspection occurred after dispatch.",
        )

    if dispatched.date() > posted:
        return finish(
            "UNCERTAIN",
            "The claimed dispatch occurred after fee posting.",
        )

    if row.get("inspection_stage") != "final_before_dispatch":
        return finish(
            "UNCERTAIN",
            "The record does not identify a final "
            "before-dispatch inspection.",
        )

    if (
        row.get("operator_verdict")
        in {"uncertain", "pending", "pending_review"}
        or row.get("processing_status")
        in {"pending", "pending_review"}
    ):
        return finish(
            "UNCERTAIN",
            "Final inspection is pending or uncertain.",
        )

    # Require an explicit source statement about continuity.
    # This is still an unverified operational assertion.
    if row.get("condition_preserved_until_dispatch") != "yes":
        return finish(
            "UNCERTAIN",
            "The record does not establish that the observed "
            "packaging condition continued until dispatch.",
        )

    state = row.get("polybag_present_sealed")

    if state == "sealed":
        return finish(
            "CONTRADICTS",
            f"Final-prep record {record_id} reports a sealed "
            "polybag before dispatch, identifies the same "
            "shipment and states that condition was preserved "
            "until dispatch. This contradicts the explicit "
            "unsealed-at-dispatch allegation. Source authenticity, "
            "applicable policy and eligibility remain unverified.",
            [record_id],
        )

    if state == "not_sealed":
        return finish(
            "SUPPORTS",
            f"Final-prep record {record_id} reports an unsealed "
            "polybag before dispatch and states that condition "
            "was preserved until dispatch, supporting the "
            "unsealed-at-dispatch allegation. Fee validity "
            "and amount are not independently established.",
            [record_id],
        )

    return finish(
        "UNCERTAIN",
        "The final-prep record does not establish seal condition.",
    )