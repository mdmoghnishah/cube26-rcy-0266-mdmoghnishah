from copy import deepcopy
import unittest

from recovery.prep_rules import assess_specific_prep_charge


def fixture():
    charge = {
        "org_id": "org_demo_alpha",
        "unit_id": "DEV-DISPATCH-UNIT",
        "sku": "DEV-SKU",
        "fnsku": "DEV-FNSKU",
        "fba_shipment_id": "DEV-SHIPMENT",
        "alleged_defect": "polybag_not_sealed",
        "allegation_scope": "at_dispatch",
        "final_prep_record_id": "DEV-FINAL-PREP",
        "dispatched_at": "2026-06-02T10:00:00Z",
        "posted_date": "2026-07-15",
    }

    row = {
        "org_id": charge["org_id"],
        "unit_id": charge["unit_id"],
        "sku": charge["sku"],
        "fnsku": charge["fnsku"],
        "fba_shipment_id": charge["fba_shipment_id"],
        "captured_at": "2026-06-02T09:00:00Z",
        "inspection_stage": "final_before_dispatch",
        "condition_preserved_until_dispatch": "yes",
        "operator_verdict": "complete",
        "polybag_present_sealed": "sealed",
    }

    evidence = [{
        "manager": "prep",
        "record_id": "DEV-FINAL-PREP",
        "raw": row,
    }]

    return charge, evidence


class ShipmentRuleTests(unittest.TestCase):
    def assess(self, charge, evidence):
        original = deepcopy((charge, evidence))
        result = assess_specific_prep_charge(charge, evidence)

        self.assertEqual((charge, evidence), original)
        self.assertIsNone(result["claim_amount_usd"])
        return result

    def test_earlier_final_inspection_contradicts(self):
        charge, evidence = fixture()
        result = self.assess(charge, evidence)

        self.assertEqual(result["assessment"], "CONTRADICTS")
        self.assertEqual(
            result["supporting_evidence_ids"],
            ["DEV-FINAL-PREP"],
        )

    def test_unsealed_final_inspection_supports(self):
        charge, evidence = fixture()
        evidence[0]["raw"]["polybag_present_sealed"] = "not_sealed"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "SUPPORTS")

    def test_after_dispatch_inspection_is_uncertain(self):
        charge, evidence = fixture()
        evidence[0]["raw"]["captured_at"] = "2026-06-02T11:00:00Z"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_wrong_shipment_is_uncertain(self):
        charge, evidence = fixture()
        evidence[0]["raw"]["fba_shipment_id"] = "OTHER-SHIPMENT"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_other_organisation_is_uncertain(self):
        charge, evidence = fixture()
        evidence[0]["raw"]["org_id"] = "org_demo_bravo"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_missing_continuity_is_uncertain(self):
        charge, evidence = fixture()
        del evidence[0]["raw"]["condition_preserved_until_dispatch"]

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_missing_final_record_link_is_uncertain(self):
        charge, evidence = fixture()
        del charge["final_prep_record_id"]

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_unavailable_referenced_record_is_silent(self):
        charge, evidence = fixture()
        evidence[0]["record_id"] = "OTHER-RECORD"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "SILENT")

    def test_duplicate_record_reference_is_uncertain(self):
        charge, evidence = fixture()
        evidence.append(deepcopy(evidence[0]))

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_pending_inspection_is_uncertain(self):
        charge, evidence = fixture()
        evidence[0]["raw"]["processing_status"] = "pending"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")

    def test_timezone_equivalent_inspection_is_accepted(self):
        charge, evidence = fixture()
        evidence[0]["raw"]["captured_at"] = (
            "2026-06-02T14:30:00+05:30"
        )

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "CONTRADICTS")

    def test_dispatch_after_posting_is_uncertain(self):
        charge, evidence = fixture()
        charge["posted_date"] = "2026-06-01"

        result = self.assess(charge, evidence)
        self.assertEqual(result["assessment"], "UNCERTAIN")


if __name__ == "__main__":
    unittest.main(verbosity=2)