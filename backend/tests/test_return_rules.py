import unittest
from copy import deepcopy

from recovery.return_rules import assess_return_charge


class ReturnRuleTests(unittest.TestCase):
    def setUp(self):
        self.charge = {
            "org_id": "org_demo_alpha",
            "unit_id": "DEV-UNIT-RETURN",
            "order_id": "DEV-ORDER-RETURN",
            "sku": "DEV-SKU-RETURN",
            "return_event_id": "DEV-RETURN-EVENT",
            "required_return_recipient": "channel_returns_center",
            "return_due_at": "2026-06-15T12:00:00Z",
            "quantity": "1",
        }

        self.record = {
            "record_id": "DEV-RETURN-RECORD",
            "manager": "returns",
            "identifier_conflicts": [],
            "raw": {
                "org_id": "org_demo_alpha",
                "unit_id": "DEV-UNIT-RETURN",
                "order_id": "DEV-ORDER-RETURN",
                "ordered_sku": "DEV-SKU-RETURN",
                "return_event_id": "DEV-RETURN-EVENT",
                "identity_match": "yes",
                "operator_disposition": "restock",
                "processing_status": "complete",
                "receipt_status": "received",
                "received_by": "channel_returns_center",
                "received_at": "2026-06-10T10:00:00Z",
                "captured_at": "2026-06-10T10:05:00Z",
                "returned_quantity": "1",
            },
        }

    def assess(self, charge_changes=None, record_changes=None):
        charge = deepcopy(self.charge)
        record = deepcopy(self.record)

        charge.update(charge_changes or {})
        record["raw"].update(record_changes or {})

        return assess_return_charge(charge, [record])

    def test_complete_receipt_contradicts(self):
        result = self.assess()

        self.assertEqual(result["assessment"], "CONTRADICTS")
        self.assertEqual(
            result["supporting_evidence_ids"],
            ["DEV-RETURN-RECORD"],
        )
        self.assertEqual(result["claim_status"], "REVIEW")
        self.assertIsNone(result["claim_amount_usd"])

    def test_no_records_is_silent(self):
        result = assess_return_charge(self.charge, [])

        self.assertEqual(result["assessment"], "SILENT")

    def test_wrong_event_is_silent(self):
        result = self.assess(
            record_changes={"return_event_id": "OTHER-EVENT"}
        )

        self.assertEqual(result["assessment"], "SILENT")

    def test_wrong_organisation_is_uncertain(self):
        result = self.assess(
            record_changes={"org_id": "org_demo_bravo"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_wrong_order_is_uncertain(self):
        result = self.assess(
            record_changes={"order_id": "OTHER-ORDER"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_wrong_item_is_uncertain(self):
        result = self.assess(
            record_changes={"ordered_sku": "OTHER-SKU"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_wrong_recipient_is_uncertain(self):
        result = self.assess(
            record_changes={"received_by": "seller_warehouse"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_pending_review_is_uncertain(self):
        result = self.assess(
            record_changes={"operator_disposition": "pending_review"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_unconfirmed_receipt_is_uncertain(self):
        result = self.assess(
            record_changes={"receipt_status": "in_transit"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_late_receipt_is_uncertain(self):
        result = self.assess(
            record_changes={
                "received_at": "2026-06-16T10:00:00Z",
                "captured_at": "2026-06-16T10:05:00Z",
            }
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_partial_quantity_is_uncertain(self):
        result = self.assess(
            charge_changes={"quantity": "2"},
            record_changes={"returned_quantity": "1"},
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_missing_deadline_is_uncertain(self):
        charge = deepcopy(self.charge)
        del charge["return_due_at"]

        result = assess_return_charge(charge, [self.record])

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_timestamp_without_timezone_is_uncertain(self):
        result = self.assess(
            record_changes={"received_at": "2026-06-10T10:00:00"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_capture_before_receipt_is_uncertain(self):
        result = self.assess(
            record_changes={"captured_at": "2026-06-10T09:59:00Z"}
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_equivalent_timezones_are_accepted(self):
        result = self.assess(
            record_changes={
                "received_at": "2026-06-10T15:30:00+05:30",
                "captured_at": "2026-06-10T15:35:00+05:30",
            }
        )

        self.assertEqual(result["assessment"], "CONTRADICTS")

    def test_duplicate_records_are_uncertain(self):
        result = assess_return_charge(
            self.charge,
            [self.record, deepcopy(self.record)],
        )

        self.assertEqual(result["assessment"], "UNCERTAIN")


if __name__ == "__main__":
    unittest.main(verbosity=2)