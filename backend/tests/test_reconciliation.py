from copy import deepcopy
import unittest

from recovery.reconciliation import reconcile_charge


def charge_fixture():
    return {
        "org_id": "org_demo_alpha",
        "line_id": "DEV-CHARGE-001",
        "unit_id": "DEV-UNIT-001",
        "charge_type": "inbound_defect_fee",
        "charge": {
            "report_type": "fee_report",
            "amount_usd": "10.00",
            "currency": "USD",
            "source_transaction_id": "DEV-TX-001",
        },
    }


def reimbursement(amount="4.00", line_id="DEV-REIM-001"):
    return {
        "org_id": "org_demo_alpha",
        "line_id": line_id,
        "unit_id": "DEV-UNIT-001",
        "charge_type": "inbound_defect_fee",
        "charge": {
            "report_type": "reimbursement_report",
            "amount_usd": amount,
            "currency": "USD",
            "applies_to_line_id": "DEV-CHARGE-001",
            "source_transaction_id": f"TX-{line_id}",
        },
    }


class ReconciliationTests(unittest.TestCase):
    def run_case(self, others):
        charge = charge_fixture()
        records = [charge, *others]
        original = deepcopy(records)

        result = reconcile_charge(charge, records)

        self.assertEqual(records, original)
        return result

    def test_no_reimbursement_does_not_invent_balance(self):
        result = self.run_case([])
        self.assertEqual(
            result["status"], "NO_LINKED_REIMBURSEMENT"
        )
        self.assertIsNone(
            result["unreimbursed_reported_amount_usd"]
        )

    def test_partial_reimbursement(self):
        result = self.run_case([reimbursement("4.00")])
        self.assertEqual(
            result["status"], "PARTIALLY_REIMBURSED"
        )
        self.assertEqual(
            result["recorded_reimbursement_usd"], "4.00"
        )
        self.assertEqual(
            result["unreimbursed_reported_amount_usd"], "6.00"
        )
        self.assertFalse(result["blocks_claim"])

    def test_full_reimbursement_blocks_new_claim(self):
        result = self.run_case([reimbursement("10.00")])
        self.assertEqual(
            result["status"], "FULLY_REIMBURSED"
        )
        self.assertTrue(result["blocks_claim"])

    def test_over_reimbursement_requires_review(self):
        result = self.run_case([reimbursement("11.00")])
        self.assertEqual(result["status"], "OVER_REIMBURSED")
        self.assertTrue(result["blocks_claim"])

    def test_multiple_distinct_payments_are_summed(self):
        result = self.run_case([
            reimbursement("4.00", "DEV-REIM-001"),
            reimbursement("3.00", "DEV-REIM-002"),
        ])
        self.assertEqual(
            result["recorded_reimbursement_usd"], "7.00"
        )
        self.assertEqual(
            result["unreimbursed_reported_amount_usd"], "3.00"
        )

    def test_duplicate_charge_line_blocks_claim(self):
        duplicate = charge_fixture()
        result = self.run_case([duplicate])
        self.assertIn("DUPLICATE_LINE_ID", result["flags"])
        self.assertTrue(result["blocks_claim"])

    def test_repeated_charge_transaction_blocks_claim(self):
        duplicate = charge_fixture()
        duplicate["line_id"] = "DEV-CHARGE-002"
        result = self.run_case([duplicate])
        self.assertIn(
            "REPEATED_SOURCE_TRANSACTION", result["flags"]
        )

    def test_duplicate_payment_line_is_not_counted_twice(self):
        payment = reimbursement()
        result = self.run_case([payment, deepcopy(payment)])
        self.assertIn(
            "DUPLICATE_REIMBURSEMENT_LINE", result["flags"]
        )
        self.assertIsNone(result["recorded_reimbursement_usd"])

    def test_duplicate_payment_transaction_is_blocked(self):
        first = reimbursement("4.00", "DEV-REIM-001")
        second = reimbursement("4.00", "DEV-REIM-002")
        second["charge"]["source_transaction_id"] = (
            first["charge"]["source_transaction_id"]
        )

        result = self.run_case([first, second])
        self.assertIn(
            "DUPLICATE_REIMBURSEMENT_TRANSACTION",
            result["flags"],
        )

    def test_unallocated_payment_requires_review(self):
        payment = reimbursement()
        del payment["charge"]["applies_to_line_id"]

        result = self.run_case([payment])
        self.assertEqual(
            result["status"], "ALLOCATION_UNCERTAIN"
        )
        self.assertTrue(result["blocks_claim"])

    def test_other_organisation_payment_is_ignored(self):
        payment = reimbursement("10.00")
        payment["org_id"] = "org_demo_bravo"

        result = self.run_case([payment])
        self.assertEqual(
            result["status"], "NO_LINKED_REIMBURSEMENT"
        )
        self.assertEqual(
            result["related_reimbursement_ids"], []
        )

    def test_other_organisation_duplicate_is_ignored(self):
        duplicate = charge_fixture()
        duplicate["org_id"] = "org_demo_bravo"

        result = self.run_case([duplicate])
        self.assertFalse(result["blocks_claim"])

    def test_linked_wrong_unit_requires_review(self):
        payment = reimbursement()
        payment["unit_id"] = "DEV-OTHER-UNIT"

        result = self.run_case([payment])
        self.assertIn(
            "REIMBURSEMENT_IDENTIFIER_CONFLICT",
            result["flags"],
        )

    def test_missing_currency_requires_review(self):
        payment = reimbursement()
        del payment["charge"]["currency"]

        result = self.run_case([payment])
        self.assertIn(
            "REIMBURSEMENT_CURRENCY_UNVERIFIED",
            result["flags"],
        )

    def test_invalid_payment_requires_review(self):
        result = self.run_case([reimbursement("NaN")])
        self.assertIn(
            "INVALID_REIMBURSEMENT_AMOUNT", result["flags"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)