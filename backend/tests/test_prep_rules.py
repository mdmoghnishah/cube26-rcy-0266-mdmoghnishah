from copy import deepcopy
import unittest

from recovery.decisions import classify


def fixture():
    charge = {
        "line_id": "DEV-FEE-001",
        "report_type": "fee_report",
        "org_id": "org_demo_alpha",
        "unit_id": "DEV-UNIT-001",
        "sku": "DEV-SKU",
        "fnsku": "DEV-FNSKU",
        "fba_shipment_id": "DEV-SHIPMENT",
        "charge_type": "inbound_defect_fee",
        "quantity": "1",
        "amount_usd": "2.00",
        "posted_date": "2026-06-20",
        "alleged_defect": "polybag_not_sealed",
        "inspection_event_id": "DEV-EVENT-001",
        "event_at": "2026-06-01T10:00:00Z",
    }

    row = {
        "record_id": "DEV-PREP-001",
        "org_id": "org_demo_alpha",
        "unit_id": "DEV-UNIT-001",
        "sku": "DEV-SKU",
        "fnsku": "DEV-FNSKU",
        "fba_shipment_id": "DEV-SHIPMENT",
        "inspection_event_id": "DEV-EVENT-001",
        "captured_at": "2026-06-01T10:00:00Z",
        "polybag_present_sealed": "sealed",
    }

    return {
        "line_id": charge["line_id"],
        "org_id": charge["org_id"],
        "unit_id": charge["unit_id"],
        "charge_type": charge["charge_type"],
        "charge": charge,
        "evidence": [{
            "record_id": row["record_id"],
            "manager": "prep",
            "captured_at": row["captured_at"],
            "identifier_conflicts": [],
            "raw": row,
        }],
    }


class PrepRuleTests(unittest.TestCase):
    def assess(self, match):
        original = deepcopy(match)
        result = classify(match, [match])

        self.assertEqual(match, original)
        self.assertIsNone(result["claim_amount_usd"])
        return result

    def test_sealed_contradicts_specific_allegation(self):
        result = self.assess(fixture())
        self.assertEqual(result["assessment"], "CONTRADICTS")
        self.assertEqual(result["claim_status"], "REVIEW")
        self.assertEqual(
            result["supporting_evidence_ids"],
            ["DEV-PREP-001"],
        )

    def test_unsealed_supports_specific_allegation(self):
        match = fixture()
        match["evidence"][0]["raw"][
            "polybag_present_sealed"
        ] = "not_sealed"

        result = self.assess(match)
        self.assertEqual(result["assessment"], "SUPPORTS")

    def test_conflicting_observations_are_uncertain(self):
        match = fixture()
        second = deepcopy(match["evidence"][0])
        second["record_id"] = "DEV-PREP-002"
        second["raw"]["record_id"] = "DEV-PREP-002"
        second["raw"]["polybag_present_sealed"] = "not_sealed"
        match["evidence"].append(second)

        result = self.assess(match)
        self.assertEqual(result["assessment"], "UNCERTAIN")
        self.assertIn(
            "CONFLICTING_OBSERVATIONS", result["flags"]
        )

    def test_wrong_event_is_silent(self):
        match = fixture()
        match["evidence"][0]["raw"][
            "inspection_event_id"
        ] = "DEV-DIFFERENT-EVENT"

        result = self.assess(match)
        self.assertEqual(result["assessment"], "SILENT")

    def test_later_observation_is_uncertain(self):
        match = fixture()
        match["evidence"][0]["raw"][
            "captured_at"
        ] = "2026-06-02T10:00:00Z"

        result = self.assess(match)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_pending_inspection_is_uncertain(self):
        match = fixture()
        match["evidence"][0]["raw"][
            "processing_status"
        ] = "pending"

        result = self.assess(match)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_missing_identifier_is_uncertain(self):
        match = fixture()
        del match["evidence"][0]["raw"]["fnsku"]

        result = self.assess(match)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_equivalent_timezones_match(self):
        match = fixture()
        match["evidence"][0]["raw"][
            "captured_at"
        ] = "2026-06-01T15:30:00+05:30"

        result = self.assess(match)
        self.assertEqual(result["assessment"], "CONTRADICTS")

    def test_generic_sample_charge_stays_uncertain(self):
        match = fixture()
        del match["charge"]["alleged_defect"]

        result = self.assess(match)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_no_evidence_is_silent(self):
        match = fixture()
        match["evidence"] = []

        result = self.assess(match)
        self.assertEqual(result["assessment"], "SILENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)