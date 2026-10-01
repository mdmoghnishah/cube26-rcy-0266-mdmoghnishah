from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection
from recovery.matcher import match_charges
from recovery.prep_rules import assess_specific_prep_charge
from recovery.reconciliation import reconcile_charge
from recovery.return_rules import assess_return_charge


def classify(match, all_matches):
    charge = match["charge"]
    evidence = match["evidence"]

    result = {
        **match,
        "assessment": "SILENT",
        "claim_status": "NOT_SUPPORTED",
        "claim_amount_usd": None,
        "currency": "USD",
        "reason": "",
        "supporting_evidence_ids": [],
        "flags": [],
        "processing_status": "complete",
        "rule_version": "sample-adapter-0.4",
    }

    def finish(assessment, reason, status="NOT_SUPPORTED"):
        result.update(
            assessment=assessment,
            reason=reason,
            claim_status=status,
        )
        return result

    def apply_evidence_rule(specific):
        existing_flags = list(result["flags"])
        specific_flags = list(specific.get("flags", []))

        result.update(specific)
        result["flags"] = list(
            dict.fromkeys(existing_flags + specific_flags)
        )

        return result

    try:
        amount = Decimal(str(charge["amount_usd"]))
        quantity = int(str(charge["quantity"]))
        date.fromisoformat(charge["posted_date"])

        if (
            not amount.is_finite()
            or amount < 0
            or amount.as_tuple().exponent < -2
            or quantity < 1
        ):
            raise ValueError("Invalid financial value")

    except (
        ValueError,
        InvalidOperation,
        KeyError,
        TypeError,
    ):
        return finish(
            "UNCERTAIN",
            "Invalid amount, quantity, or posting date.",
            "REVIEW",
        )

    report_type = charge.get("report_type")

    if report_type == "reimbursement_report":
        return finish(
            "SILENT",
            "This is a reimbursement entry, not a new charge.",
            "REIMBURSEMENT_RECORDED",
        )

    if report_type not in {
        "fee_report",
        "inventory_adjustment",
    }:
        return finish(
            "UNCERTAIN",
            "Unknown report type.",
            "REVIEW",
        )

    reconciliation = reconcile_charge(match, all_matches)

    result["reconciliation"] = reconciliation
    result["flags"].extend(reconciliation["flags"])
    result["related_reimbursement_ids"] = (
        reconciliation["related_reimbursement_ids"]
    )

    if reconciliation["status"] == "FULLY_REIMBURSED":
        return finish(
            "UNCERTAIN",
            reconciliation["reason"]
            + " This reconciliation does not determine "
            "whether the original allegation was correct.",
            "ALREADY_REIMBURSED",
        )

    if reconciliation["blocks_claim"]:
        return finish(
            "UNCERTAIN",
            reconciliation["reason"],
            "REVIEW",
        )

    if any(
        item.get("identifier_conflicts")
        for item in evidence
    ):
        return finish(
            "UNCERTAIN",
            "Unit matched, but other identifiers conflict.",
            "REVIEW",
        )

    charge_type = charge.get("charge_type")

    if charge_type == "fulfilment_fee_weight_tier":
        return finish(
            "SILENT",
            "No measured weight, dimensions, or applicable "
            "fee schedule is available.",
        )

    if not evidence:
        return finish(
            "SILENT",
            "No relevant evidence for this organization and unit.",
        )

    if charge_type == "inbound_defect_fee":
        specific = assess_specific_prep_charge(charge, evidence)

        if specific is not None:
            return apply_evidence_rule(specific)

    if charge_type == "refund_issued_item_not_returned":
        return apply_evidence_rule(
            assess_return_charge(charge, evidence)
        )

    reasons = {
        "inbound_defect_fee": (
            "The report does not specify a supported alleged "
            "defect or event time. Prep observations alone "
            "cannot settle the charge."
        ),
        "lost_inbound": (
            "Supplier receiving/prep records do not prove "
            "channel receipt or loss. Delivery evidence "
            "and valuation are missing."
        ),
        "damaged_in_warehouse": (
            "Condition records do not establish custody "
            "or responsibility for the damage."
        ),
    }

    return finish(
        "UNCERTAIN",
        reasons.get(
            charge_type,
            "No decision rule is configured for this charge.",
        ),
        "REVIEW",
    )


def assess_organization(org_id):
    matches = match_charges(org_id)
    results = [
        classify(match, matches)
        for match in matches
    ]

    with connection(org_id) as conn:
        for result in results:
            conn.execute(
                """
                INSERT INTO recovery.decisions (
                    id, org_id, result
                )
                VALUES (%s, %s, %s)
                """,
                (
                    uuid4(),
                    org_id,
                    Jsonb(result),
                ),
            )

    return results


if __name__ == "__main__":
    for organization in (
        "org_demo_alpha",
        "org_demo_bravo",
    ):
        results = assess_organization(organization)
        counts = Counter(
            item["assessment"]
            for item in results
        )

        print(
            organization,
            f"saved={len(results)}",
            dict(counts),
        )