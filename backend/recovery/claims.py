from decimal import Decimal, InvalidOperation


def parse_money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid monetary value.")

    if (
        not amount.is_finite()
        or amount < 0
        or amount.as_tuple().exponent < -2
    ):
        raise ValueError("Invalid monetary value.")

    return amount


def build_potential_claim(result):
    charge = result.get("charge", {})
    reconciliation = result.get("reconciliation") or {}

    candidate = {
        "status": "NOT_SUPPORTED",
        "potential_amount_usd": None,
        "approved_amount_usd": None,
        "currency": "USD",
        "submission_ready": False,
        "amount_basis": None,
        "evidence_ids": [],
        "reason": "",
        "required_verifications": [
            "Evidence authenticity",
            "Applicable channel requirement and effective date",
            "Channel dispute eligibility and deadline",
            "Complete duplicate and reimbursement reconciliation",
            "Actual financial charge and recoverable amount",
        ],
    }

    def reject(reason, status="NOT_SUPPORTED"):
        candidate["status"] = status
        candidate["reason"] = reason
        return candidate

    if charge.get("report_type") != "fee_report":
        return reject(
            "This calculation only supports reported fees. "
            "Inventory losses require a separate valuation basis."
        )

    if result.get("claim_status") == "ALREADY_REIMBURSED":
        return reject(
            "Linked reimbursements leave no reported balance.",
            "ALREADY_REIMBURSED",
        )

    if reconciliation.get("blocks_claim"):
        return reject(
            reconciliation.get("reason")
            or "Financial reconciliation requires review.",
            "REVIEW",
        )

    if result.get("assessment") != "CONTRADICTS":
        return reject(
            "The original evidence assessment does not "
            "contradict the fee."
        )

    ids = result.get("supporting_evidence_ids") or []
    available = {
        record.get("record_id")
        for record in result.get("evidence", [])
    }

    if not ids or not set(ids).issubset(available):
        return reject(
            "Supporting evidence references are missing "
            "or cannot be resolved.",
            "REVIEW",
        )

    # Existing sample columns explicitly identify USD amounts.
    # An explicitly different currency is unsupported.
    if charge.get("currency", "USD") != "USD":
        return reject(
            "This calculation supports USD reports only.",
            "REVIEW",
        )

    if result.get("flags"):
        return reject(
            "Assessment flags require resolution before "
            "calculating a potential claim.",
            "REVIEW",
        )

    status = reconciliation.get("status")

    try:
        reported = parse_money(charge.get("amount_usd"))

        if status == "PARTIALLY_REIMBURSED":
            reimbursed = parse_money(
                reconciliation.get("recorded_reimbursement_usd")
            )
            remaining = parse_money(
                reconciliation.get(
                    "unreimbursed_reported_amount_usd"
                )
            )

            if reimbursed + remaining != reported:
                return reject(
                    "Reconciliation amounts do not agree "
                    "with the reported fee.",
                    "REVIEW",
                )

            potential = remaining
            basis = (
                "Reported fee minus explicitly linked "
                "reimbursements."
            )

        elif status == "NO_LINKED_REIMBURSEMENT":
            potential = reported
            basis = (
                "Reported fee; no linked reimbursement was "
                "found in the imported records. Completeness "
                "of those records is not established."
            )

        else:
            return reject(
                "A supported reconciliation result is required.",
                "REVIEW",
            )

    except ValueError:
        return reject(
            "Financial amounts are invalid or incomplete.",
            "REVIEW",
        )

    if potential == 0:
        return reject("No positive reported balance remains.")

    dataset = charge.get(
        "dataset", "organiser-synthetic-reference"
    )

    synthetic = dataset in {
        "synthetic-development-v1",
        "organiser-synthetic-reference",
    }

    candidate.update(
        status=(
            "SYNTHETIC_POTENTIAL_CLAIM"
            if synthetic
            else "PROVISIONAL_POTENTIAL_CLAIM"
        ),
        potential_amount_usd=format(potential, ".2f"),
        amount_basis=basis,
        evidence_ids=sorted(set(ids)),
        reason=(
            "Cited evidence contradicts the fee. The potential "
            "amount uses the remaining reported fee balance; "
            "it is conditional on the outstanding verifications."
        ),
    )

    return candidate