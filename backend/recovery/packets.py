from copy import deepcopy
from datetime import datetime, timezone


def build_review_packet(decision):
    result = decision["result"]
    charge = result.get("charge", {})
    assessment = result.get("assessment", "UNCERTAIN")

    cited_ids = set(
        result.get("supporting_evidence_ids", [])
    )

    matched = result.get("evidence", [])
    cited = [
        deepcopy(record)
        for record in matched
        if record.get("record_id") in cited_ids
    ]

    available_ids = {
        record.get("record_id") for record in matched
    }

    unresolved_ids = sorted(cited_ids - available_ids)

    dataset = charge.get(
        "dataset", "organiser-synthetic-reference"
    )

    is_synthetic = dataset in {
        "synthetic-development-v1",
        "organiser-synthetic-reference",
    }

    if assessment == "CONTRADICTS":
        recommendation = "REVIEW_FOR_POTENTIAL_DISPUTE"
    elif assessment == "SUPPORTS":
        recommendation = "EVIDENCE_SUPPORTS_ALLEGATION"
    else:
        recommendation = "GATHER_MORE_INFORMATION"

    outstanding = [
        {
            "check": "evidence_authenticity",
            "status": "NOT_VERIFIED",
            "description": (
                "Verify source records and associated evidence."
            ),
        },
        {
            "check": "authoritative_policy",
            "status": "NOT_VERIFIED",
            "description": (
                "Retrieve applicable channel requirements, "
                "eligibility rules and effective dates."
            ),
        },
        {
            "check": "duplicate_and_reimbursement_reconciliation",
            "status": "NOT_VERIFIED",
            "description": (
                "Confirm this event has not already been "
                "claimed or reimbursed, including partial payments."
            ),
        },
        {
            "check": "recoverable_amount",
            "status": "NOT_ESTABLISHED",
            "description": (
                "Verify the actual charge, applicable recovery "
                "basis and remaining eligible amount."
            ),
        },
    ]

    if unresolved_ids:
        outstanding.append({
            "check": "missing_cited_records",
            "status": "FAILED",
            "description": (
                "Cited evidence is absent from the decision: "
                + ", ".join(unresolved_ids)
            ),
        })

    return {
        "packet_type": "RECOVERY_REVIEW_PACKET",
        "packet_version": "0.1",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "decision_id": str(decision["id"]),
        "decision_created_at": str(
            decision.get("created_at", "")
        ),
        "org_id": result.get("org_id"),
        "line_id": result.get("line_id"),
        "unit_id": result.get("unit_id"),
        "dataset": dataset,
        "synthetic_data": is_synthetic,
        "source_data_notice": (
            "Current datasets are synthetic; this packet "
            "does not establish a real marketplace event."
        ),
        "charge_type": result.get("charge_type"),
        "allegation": charge.get("alleged_defect"),
        "inspection_event_id": charge.get(
            "inspection_event_id"
        ),
        "event_at": charge.get("event_at"),
        "reported_amount_usd": charge.get("amount_usd"),
        "currency": result.get("currency", "USD"),
        "agent_assessment": assessment,
        "explanation": result.get("reason"),
        "recommendation": recommendation,
        "claim_ready": False,
        "approved_claim_amount_usd": None,
        "outstanding_checks": outstanding,
        "flags": list(result.get("flags", [])),
        "related_reimbursement_ids": list(
            result.get("related_reimbursement_ids", [])
        ),
        "cited_evidence": cited,
        "original_report_row": deepcopy(charge),
        "human_reviews": deepcopy(
            decision.get("reviews", [])
        ),
        "rule_version": result.get("rule_version"),
    }