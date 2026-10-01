from decimal import Decimal, InvalidOperation


def money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid monetary value.")

    if (
        not amount.is_finite()
        or amount < 0
        or amount.as_tuple().exponent < -2
    ):
        raise ValueError("Invalid monetary value.")

    return amount


def reconcile_charge(match, all_matches):
    charge = match["charge"]
    org_id = match["org_id"]
    line_id = match["line_id"]

    # Never compare another organisation's records.
    scoped = [
        item for item in all_matches
        if item.get("org_id") == org_id
    ]

    outcome = {
        "status": "NO_LINKED_REIMBURSEMENT",
        "flags": [],
        "related_reimbursement_ids": [],
        "recorded_reimbursement_usd": None,
        "unreimbursed_reported_amount_usd": None,
        "blocks_claim": False,
        "reason": "",
    }

    def block(status, flag, reason):
        outcome.update(
            status=status,
            blocks_claim=True,
            reason=reason,
        )
        outcome["flags"].append(flag)
        return outcome

    same_line = [
        item for item in scoped
        if item.get("line_id") == line_id
    ]

    if len(same_line) > 1:
        return block(
            "DUPLICATE",
            "DUPLICATE_LINE_ID",
            "Multiple source rows use this report-line ID. "
            "Resolve the duplicate before claiming.",
        )

    # Optional development field: the channel's financial
    # transaction identifier, not a unit or shipment identifier.
    transaction_id = charge.get("source_transaction_id")

    if transaction_id:
        same_transaction = [
            item for item in scoped
            if (
                item["charge"].get("report_type")
                in {"fee_report", "inventory_adjustment"}
                and item["charge"].get("source_transaction_id")
                == transaction_id
            )
        ]

        if len(same_transaction) > 1:
            return block(
                "DUPLICATE",
                "REPEATED_SOURCE_TRANSACTION",
                "Multiple charge rows reference the same "
                "source transaction. Resolve before claiming.",
            )

    reimbursements = [
        item for item in scoped
        if item["charge"].get("report_type")
        == "reimbursement_report"
    ]

    linked = [
        item for item in reimbursements
        if item["charge"].get("applies_to_line_id") == line_id
    ]

    possible = [
        item for item in reimbursements
        if (
            not item["charge"].get("applies_to_line_id")
            and item.get("unit_id") == match.get("unit_id")
            and item.get("charge_type") == match.get("charge_type")
        )
    ]

    outcome["related_reimbursement_ids"] = sorted({
        item["line_id"] for item in linked + possible
    })

    if possible:
        return block(
            "ALLOCATION_UNCERTAIN",
            "POSSIBLE_REIMBURSEMENT",
            "A reimbursement matches the unit and charge type "
            "but does not explicitly reference this charge. "
            "Its allocation requires review.",
        )

    if not linked:
        # Absence from imported reports does not prove that
        # no reimbursement exists elsewhere.
        return outcome

    linked_ids = [item["line_id"] for item in linked]

    if len(linked_ids) != len(set(linked_ids)):
        return block(
            "ALLOCATION_UNCERTAIN",
            "DUPLICATE_REIMBURSEMENT_LINE",
            "Linked reimbursement line IDs repeat. "
            "Do not sum them until duplicates are resolved.",
        )

    reimbursement_transactions = [
        item["charge"].get("source_transaction_id")
        for item in linked
        if item["charge"].get("source_transaction_id")
    ]

    if len(reimbursement_transactions) != len(
        set(reimbursement_transactions)
    ):
        return block(
            "ALLOCATION_UNCERTAIN",
            "DUPLICATE_REIMBURSEMENT_TRANSACTION",
            "Linked reimbursements repeat a source transaction. "
            "Do not count the transaction twice.",
        )

    try:
        reported = money(charge.get("amount_usd"))
        total = Decimal("0.00")

        for item in linked:
            row = item["charge"]

            if (
                item.get("unit_id") != match.get("unit_id")
                or item.get("charge_type") != match.get("charge_type")
            ):
                return block(
                    "ALLOCATION_UNCERTAIN",
                    "REIMBURSEMENT_IDENTIFIER_CONFLICT",
                    "An explicitly linked reimbursement has "
                    "a different unit or charge type.",
                )

            # Require an explicit currency for arithmetic.
            if (
                charge.get("currency") != "USD"
                or row.get("currency") != "USD"
            ):
                return block(
                    "ALLOCATION_UNCERTAIN",
                    "REIMBURSEMENT_CURRENCY_UNVERIFIED",
                    "Explicit USD currency is required on both "
                    "the charge and linked reimbursements.",
                )

            total += money(row.get("amount_usd"))

    except ValueError:
        return block(
            "ALLOCATION_UNCERTAIN",
            "INVALID_REIMBURSEMENT_AMOUNT",
            "Charge or reimbursement amount is invalid.",
        )

    outcome["recorded_reimbursement_usd"] = format(total, ".2f")

    if total > reported:
        return block(
            "OVER_REIMBURSED",
            "REIMBURSEMENT_EXCEEDS_CHARGE",
            "Linked reimbursements exceed the reported charge. "
            "Review allocation and source records.",
        )

    remaining = reported - total
    outcome["unreimbursed_reported_amount_usd"] = format(
        remaining, ".2f"
    )

    if total == reported:
        outcome.update(
            status="FULLY_REIMBURSED",
            blocks_claim=True,
            reason=(
                "Explicitly linked reimbursements equal the "
                "reported charge. No remaining reported balance."
            ),
        )
    else:
        outcome.update(
            status="PARTIALLY_REIMBURSED",
            reason=(
                "Explicitly linked reimbursements leave a "
                "reported balance. This is not an approved "
                "recoverable amount."
            ),
        )

    return outcome