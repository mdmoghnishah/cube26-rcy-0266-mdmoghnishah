from copy import deepcopy
import unittest

from recovery.claims import build_potential_claim


def fixture():
    return {
        "assessment": "CONTRADICTS",
        "claim_status": "REVIEW",
        "flags": [],
        "charge": {
            "report_type": "fee_report",
            "amount_usd": "10.00",
            "currency": "USD",
            "dataset": "synthetic-development-v1",
        },
        "supporting_evidence_ids": ["DEV-PREP-001"],
        "evidence": [{"record_id": "DEV-PREP-001"}],
        "reconciliation": {
            "status": "PARTIALLY_REIMBURSED",
            "blocks_claim": False,
            "recorded_reimbursement_usd": "4.00",
            "unreimbursed_reported_amount_usd": "6.00",
        },
    }


class PotentialClaimTests(unittest.TestCase):
    def calculate(self, result):
        original = deepcopy(result)
        claim = build_potential_claim(result)

        self.assertEqual(result, original)
        self.assertIsNone(claim["approved_amount_usd"])
        self.assertFalse(claim["submission_ready"])
        return claim

    def test_partial_reimbursement_leaves_six_dollars(self):
        claim = self.calculate(fixture())
        self.assertEqual(claim["potential_amount_usd"], "6.00")
        self.assertEqual(
            claim["status"], "SYNTHETIC_POTENTIAL_CLAIM"
        )

    def test_no_linked_payment_uses_reported_fee_conditionally(self):
        result = fixture()
        result["reconciliation"] = {
            "status": "NO_LINKED_REIMBURSEMENT",
            "blocks_claim": False,
        }

        claim = self.calculate(result)
        self.assertEqual(claim["potential_amount_usd"], "10.00")
        self.assertIn(
            "Completeness", claim["amount_basis"]
        )

    def test_full_reimbursement_blocks_potential_claim(self):
        result = fixture()
        result["claim_status"] = "ALREADY_REIMBURSED"

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "ALREADY_REIMBURSED")
        self.assertIsNone(claim["potential_amount_usd"])

    def test_duplicate_blocks_potential_claim(self):
        result = fixture()
        result["reconciliation"]["blocks_claim"] = True
        result["reconciliation"]["reason"] = "Duplicate transaction."

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "REVIEW")
        self.assertIsNone(claim["potential_amount_usd"])

    def test_noncontradicting_assessments_have_no_amount(self):
        for assessment in ("SUPPORTS", "SILENT", "UNCERTAIN"):
            with self.subTest(assessment=assessment):
                result = fixture()
                result["assessment"] = assessment

                claim = self.calculate(result)
                self.assertIsNone(claim["potential_amount_usd"])

    def test_missing_cited_evidence_blocks_calculation(self):
        result = fixture()
        result["evidence"] = []

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "REVIEW")
        self.assertIsNone(claim["potential_amount_usd"])

    def test_inconsistent_reconciliation_blocks_calculation(self):
        result = fixture()
        result["reconciliation"][
            "unreimbursed_reported_amount_usd"
        ] = "8.00"

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "REVIEW")
        self.assertIsNone(claim["potential_amount_usd"])

    def test_invalid_amount_is_rejected(self):
        result = fixture()
        result["charge"]["amount_usd"] = "NaN"

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "REVIEW")

    def test_foreign_currency_is_rejected(self):
        result = fixture()
        result["charge"]["currency"] = "EUR"

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "REVIEW")

    def test_inventory_loss_requires_separate_valuation(self):
        result = fixture()
        result["charge"]["report_type"] = "inventory_adjustment"

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "NOT_SUPPORTED")

    def test_assessment_flags_block_calculation(self):
        result = fixture()
        result["flags"] = ["IDENTIFIER_CONFLICT"]

        claim = self.calculate(result)
        self.assertEqual(claim["status"], "REVIEW")


if __name__ == "__main__":
    unittest.main(verbosity=2)