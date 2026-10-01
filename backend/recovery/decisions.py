from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection
from recovery.matcher import match_charges


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
        "rule_version": "sample-adapter-0.1",
    }

    def finish(assessment, reason, status="NOT_SUPPORTED"):
        result.update(
            assessment=assessment,
            reason=reason,
            claim_status=status,
        )
        return result

    try:
        amount = Decimal(charge["amount_usd"])
        quantity = int(charge["quantity"])
        posted = date.fromisoformat(charge["posted_date"])

        if (
            not amount.is_finite()
            or amount < 0
            or amount.as_tuple().exponent < -2
            or quantity < 1
        ):
            raise ValueError("Invalid financial value")
    except (ValueError, InvalidOperation):
        return finish(
            "UNCERTAIN",
            "Invalid amount, quantity, or posting date.",
            "REVIEW",
        )

    if charge["report_type"] == "reimbursement_report":
        return finish(
            "SILENT",
            "This is a reimbursement entry, not a new charge.",
            "REIMBURSEMENT_RECORDED",
        )

    if charge["report_type"] not in {
        "fee_report",
        "inventory_adjustment",
    }:
        return finish(
            "UNCERTAIN",
            "Unknown report type.",
            "REVIEW",
        )

    duplicate_count = sum(
        item["line_id"] == match["line_id"]
        for item in all_matches
    )

    if duplicate_count > 1:
        result["flags"].append("DUPLICATE_LINE_ID")
        return finish(
            "UNCERTAIN",
            "Repeated line ID; review before claiming.",
            "REVIEW",
        )

    related_reimbursements = [
        item["line_id"]
        for item in all_matches
        if (
            item["charge"]["report_type"]
            == "reimbursement_report"
            and item["unit_id"] == match["unit_id"]
            and item["charge_type"] == match["charge_type"]
        )
    ]

    if related_reimbursements:
        result["flags"].append("POSSIBLE_REIMBURSEMENT")
        result["related_reimbursement_ids"] = (
            related_reimbursements
        )
        return finish(
            "UNCERTAIN",
            "A related reimbursement exists, but allocation "
            "to this charge is not established.",
            "REVIEW",
        )

    if any(
        item["identifier_conflicts"]
        for item in evidence
    ):
        return finish(
            "UNCERTAIN",
            "Unit matched, but other identifiers conflict.",
            "REVIEW",
        )

    charge_type = charge["charge_type"]

    if charge_type == "fulfilment_fee_weight_tier":
        return finish(
            "SILENT",
            "No measured weight, dimensions, or applicable "
            "fee schedule is available.",
        )

    if not evidence:
        return finish(
            "SILENT",
            "No relevant evidence for this organization "
            "and unit.",
        )

    if charge_type == "refund_issued_item_not_returned":
        if not charge.get("order_id") or not charge.get("sku"):
            return finish(
                "UNCERTAIN",
                "Order ID and SKU are required.",
                "REVIEW",
            )

        supporting_ids = []

        for item in evidence:
            row = item["raw"]

            try:
                captured = datetime.fromisoformat(
                    row["captured_at"].replace("Z", "+00:00")
                )

                if captured.tzinfo is None:
                    raise ValueError("Missing timezone")

                captured_date = captured.date()
            except (ValueError, KeyError):
                return finish(
                    "UNCERTAIN",
                    "Return timestamp is missing or invalid.",
                    "REVIEW",
                )

            if (
                row.get("identity_match") != "yes"
                or row.get("operator_disposition")
                == "pending_review"
                or row.get("order_id") != charge["order_id"]
                or row.get("ordered_sku") != charge["sku"]
            ):
                return finish(
                    "UNCERTAIN",
                    "Returned-item identity or review state "
                    "is insufficient.",
                    "REVIEW",
                )

            if captured_date >= posted:
                return finish(
                    "UNCERTAIN",
                    "Return was recorded on or after the "
                    "posting date. Timing needs review.",
                    "REVIEW",
                )

            supporting_ids.append(item["record_id"])

        if quantity != 1:
            return finish(
                "UNCERTAIN",
                "Return record lacks a count to verify "
                "this multi-unit charge.",
                "REVIEW",
            )

        result["supporting_evidence_ids"] = supporting_ids

        return finish(
            "CONTRADICTS",
            "Matching item was recorded returned before "
            "posting. Channel eligibility and claim amount "
            "still require verification.",
            "REVIEW",
        )

    reasons = {
        "inbound_defect_fee": (
            "The report does not specify the alleged defect "
            "or event time. Prep observations alone cannot "
            "settle the charge."
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
            item["assessment"] for item in results
        )

        print(
            organization,
            f"saved={len(results)}",
            dict(counts),
        )