"""Round 3 adapter: envelope, outcome mapping, fail-open, tenancy, overrides."""

import json
import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient

from recovery import round3
from recovery.api import app
from recovery.round3 import (
    POSITION_TO_VERDICT,
    charge_check,
    handle,
    run_round3,
    verify_hash,
)

ORG = "org_demo_alpha"
UNIT = "UNIT-TEST-1"


def record(stage, record_id, *, status="completed", verdict="PASS", outcome="compliant",
           checks=None, refs=None, org=ORG, unit=UNIT):
    return {
        "schema_version": "1.0",
        "record_id": record_id,
        "workflow_id": f"WF-{org}-{unit}",
        "stage": stage,
        "agent_id": f"{stage}-test@0",
        "subject": {"org_id": org, "subject_id": unit, "unit_id": unit, "unit_scope": "unit", "refs": refs or {}},
        "status": status,
        "captured_at": "2026-06-06T07:36:00Z",
        "produced_at": "2026-10-04T18:37:05Z",
        "model": {"name": "rules", "version": "0"},
        "inputs": [],
        "checks": checks or [],
        "decision": {"verdict": verdict, "outcome": outcome, "reason": "test", "needs_human": False},
        "payload": {},
        "upstream_refs": [],
        "overrides": [],
        "error": None,
        "content_hash": "0" * 64,
    }


def prep_record(verdict="PASS", seal="PASS", **kw):
    return record(
        "prep", "PRP-T1", verdict=verdict,
        outcome="compliant" if verdict == "PASS" else "non_compliant",
        checks=[
            {"check_key": "polybag_sealed", "verdict": seal, "confidence": None},
            {"check_key": "fnsku_label_placement", "verdict": verdict, "confidence": None},
        ],
        refs={"sku": "SKU-T", "fnsku": "X00T", "fba_shipment_id": "FBA-T"},
        **kw,
    )


def returns_record(identity="PASS", outcome="restock", status="completed", **kw):
    return record(
        "returns", "RTN-T1", status=status, outcome=outcome,
        checks=[{"check_key": "identity_match", "verdict": identity, "confidence": None}],
        refs={"order_id": "ORD-T", "sku": "SKU-T"},
        **kw,
    )


def charge(line_id, charge_type, amount="2.00", **extra):
    row = {
        "line_id": line_id, "report_type": "fee_report", "unit_id": UNIT, "org_id": ORG,
        "sku": "SKU-T", "fnsku": "X00T", "fba_shipment_id": "FBA-T", "order_id": "ORD-T",
        "charge_type": charge_type, "quantity": "1", "amount_usd": amount, "posted_date": "2026-07-18",
    }
    row.update(extra)
    return row


def agent_input(charges, previous=None, overrides=None, request_id="req-1", **kw):
    body = {
        "schema_version": "1.0",
        "request_id": request_id,
        "workflow_id": f"WF-{ORG}-{UNIT}",
        "stage": "recovery",
        "subject": {"org_id": ORG, "subject_id": UNIT, "route": "fba"},
        "inputs": [],
        "previous_evidence": previous or [],
        "context": {"overrides": overrides or [], "charges": charges, "include_ai_summary": False},
    }
    body.update(kw)
    return body


class Round3Tests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(round3, "_persist", lambda record_id, output: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"RECOVERY_ROUND3_CACHE": "off"})
        env.start()
        self.addCleanup(env.stop)

    # -- envelope ---------------------------------------------------------

    def test_envelope_shape_and_hash(self):
        code, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()]))
        self.assertEqual(code, 200)
        self.assertEqual(out["schema_version"], "1.0")
        self.assertEqual(out["stage"], "recovery")
        self.assertEqual(out["agent_id"], "recovery-manager@1.0.0")
        self.assertIn(out["status"], {"completed", "pending"})
        self.assertIn(out["verdict"], {"PASS", "FAIL", "UNCERTAIN"})
        ev = out["evidence"]
        self.assertTrue(ev["record_id"].startswith("RCY-"))
        self.assertEqual(ev["subject"]["org_id"], ORG)
        self.assertEqual(ev["subject"]["subject_id"], UNIT)
        self.assertEqual(ev["workflow_id"], out["workflow_id"])
        self.assertEqual(ev["decision"]["verdict"], out["verdict"])
        self.assertEqual(ev["status"], out["status"])
        self.assertEqual(ev["upstream_refs"], ["PRP-T1"])
        self.assertEqual(len(ev["content_hash"]), 64)
        self.assertTrue(verify_hash(ev))
        for check in ev["checks"]:
            self.assertRegex(check["check_key"], r"^[a-z][a-z0-9_]*$")
            if check["verdict"] == "UNCERTAIN":
                self.assertIn("uncertain_reason", check)
        self.assertIsNone(out["error"])
        self.assertFalse(ev["payload"]["claim_filed"])

    def test_same_request_same_record_id(self):
        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        first = run_round3(deepcopy(body))[1]["evidence"]["record_id"]
        second = run_round3(deepcopy(body))[1]["evidence"]["record_id"]
        self.assertEqual(first, second)
        other = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()], request_id="req-2"))
        self.assertNotEqual(first, other[1]["evidence"]["record_id"])

    def test_captured_at_is_not_now(self):
        _, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()]))
        self.assertEqual(out["evidence"]["captured_at"], "2026-07-18T00:00:00Z")

    # -- outcome mapping ----------------------------------------------------

    def test_position_to_verdict_table(self):
        self.assertEqual(POSITION_TO_VERDICT["CONTRADICTS"], "FAIL")
        self.assertEqual(POSITION_TO_VERDICT["SUPPORTS"], "PASS")
        self.assertEqual(POSITION_TO_VERDICT["SILENT"], "UNCERTAIN")
        self.assertEqual(POSITION_TO_VERDICT["UNCERTAIN"], "UNCERTAIN")
        silent = charge_check({"line_id": "FEE-1", "assessment": "SILENT", "reason": "x", "supporting_evidence_ids": []})
        self.assertEqual(silent["uncertain_reason"], "insufficient_evidence")
        conflict = charge_check({"line_id": "FEE-1", "assessment": "UNCERTAIN", "reason": "x",
                                 "supporting_evidence_ids": [], "flags": ["CONFLICTING_OBSERVATIONS"]})
        self.assertEqual(conflict["uncertain_reason"], "conflicting_evidence")

    def test_prep_pass_contradicts_generic_defect_fee(self):
        _, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()]))
        ev = out["evidence"]
        self.assertEqual(out["verdict"], "FAIL")
        self.assertEqual(ev["decision"]["outcome"], "claim_recommended")
        self.assertTrue(ev["decision"]["needs_human"], "a potential claim still needs a person before filing")
        check = ev["checks"][0]
        self.assertEqual(check["check_key"], "charge_fee_t_1")
        self.assertEqual((check["verdict"], check["observed"]), ("FAIL", "CONTRADICTS"))
        self.assertEqual(check["evidence_refs"], ["PRP-T1"])
        self.assertEqual(ev["payload"]["claimable_usd"], 2.0)
        self.assertEqual(ev["payload"]["unclaimable"], [])

    def test_specific_allegation_uses_matching_prep_check(self):
        sealed = run_round3(agent_input(
            [charge("FEE-T-1", "inbound_defect_fee", alleged_defect="polybag_not_sealed")],
            [prep_record(seal="PASS")],
        ))[1]["evidence"]["payload"]["charges"][0]
        unsealed = run_round3(agent_input(
            [charge("FEE-T-1", "inbound_defect_fee", alleged_defect="polybag_not_sealed")],
            [prep_record(seal="FAIL")],
        ))[1]["evidence"]["payload"]["charges"][0]
        unchecked = run_round3(agent_input(
            [charge("FEE-T-1", "inbound_defect_fee", alleged_defect="missing_suffocation_warning")],
            [prep_record()],
        ))[1]["evidence"]["payload"]["charges"][0]
        self.assertEqual(sealed["position"], "CONTRADICTS")
        self.assertEqual(unsealed["position"], "SUPPORTS")
        self.assertEqual(unchecked["position"], "SILENT")

    def test_prep_fail_supports_fee_and_all_supports_is_no_claim(self):
        _, out = run_round3(agent_input(
            [charge("FEE-T-1", "inbound_defect_fee")], [prep_record(verdict="FAIL", seal="FAIL")],
        ))
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["evidence"]["decision"]["outcome"], "no_claim")
        self.assertEqual(out["evidence"]["payload"]["claimable_usd"], 0.0)

    def test_silent_never_becomes_a_claim(self):
        _, out = run_round3(agent_input([
            charge("FEE-T-2", "fulfilment_fee_weight_tier", amount="4.75"),
            charge("FEE-T-3", "lost_inbound", amount="0.00", report_type="inventory_adjustment"),
        ], [record("receiving", "RCV-T1", outcome="accept")]))
        ev = out["evidence"]
        self.assertEqual(out["verdict"], "UNCERTAIN")
        self.assertEqual(ev["decision"]["outcome"], "insufficient_evidence")
        self.assertFalse(ev["decision"]["needs_human"], "SILENT is not a request for a person")
        self.assertEqual([c["position"] for c in ev["payload"]["charges"]], ["SILENT", "SILENT"])
        self.assertEqual(ev["payload"]["claimable_usd"], 0.0)
        self.assertEqual(len(ev["payload"]["unclaimable"]), 2)

    def test_zero_amount_contradiction_is_silent(self):
        _, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee", amount="0.00")], [prep_record()]))
        row = out["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "SILENT")
        self.assertIn("F-09", row["reason"])
        self.assertEqual(out["evidence"]["payload"]["claimable_usd"], 0.0)

    def test_returns_identity_pass_contradicts_not_returned_fee(self):
        _, out = run_round3(agent_input(
            [charge("FEE-T-4", "refund_issued_item_not_returned", amount="12.00")], [returns_record()],
        ))
        row = out["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "CONTRADICTS")
        self.assertEqual(row["evidence_record_ids"], ["RTN-T1"])
        self.assertEqual(out["evidence"]["decision"]["outcome"], "claim_recommended")

    def test_returns_pending_review_is_silent(self):
        _, out = run_round3(agent_input(
            [charge("FEE-T-4", "refund_issued_item_not_returned", amount="12.00")],
            [returns_record(identity="UNCERTAIN", outcome="pending_review", status="pending")],
        ))
        self.assertEqual(out["evidence"]["payload"]["charges"][0]["position"], "SILENT")

    def test_override_of_prep_changes_position(self):
        base = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        override = {
            "override_id": "OVR-1", "supersedes": {"record_id": "PRP-T1", "override_id": None},
            "target": "decision", "actor": "t", "at": "2026-01-01T00:00:00Z", "reason": "label creased",
            "original_verdict": "PASS", "previous_verdict": "PASS", "new_verdict": "FAIL",
        }
        before = run_round3(deepcopy(base))[1]["evidence"]["payload"]["charges"][0]["position"]
        after = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()], [override]))
        self.assertEqual(before, "CONTRADICTS")
        self.assertEqual(after[1]["evidence"]["payload"]["charges"][0]["position"], "SUPPORTS")
        self.assertEqual(after[1]["evidence"]["payload"]["overrides_applied"], ["PRP-T1"])

    def test_round2_rules_run_first_and_reconciliation_blocks_claim(self):
        reimbursement = charge("REIMB-T-1", "inbound_defect_fee", amount="2.00",
                               report_type="reimbursement_report", applies_to_line_id="FEE-T-1", currency="USD")
        fee = charge("FEE-T-1", "inbound_defect_fee", currency="USD")
        _, out = run_round3(agent_input([fee, reimbursement], [prep_record()]))
        rows = {c["line_id"]: c for c in out["evidence"]["payload"]["charges"]}
        self.assertEqual(rows["FEE-T-1"]["claim_status"], "ALREADY_REIMBURSED")
        self.assertEqual(rows["FEE-T-1"]["rule_path"], "round2_rules")
        self.assertEqual(rows["REIMB-T-1"]["position"], "SILENT")
        self.assertEqual(out["evidence"]["payload"]["claimable_usd"], 0.0)
        self.assertNotEqual(out["evidence"]["decision"]["outcome"], "claim_recommended")

    # -- fail open ----------------------------------------------------------

    def test_missing_fee_lines_is_uncertain_not_error(self):
        _, out = run_round3(agent_input([], [prep_record()]))
        ev = out["evidence"]
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["verdict"], "UNCERTAIN")
        self.assertEqual(ev["decision"]["outcome"], "insufficient_evidence")
        self.assertEqual(ev["checks"][0]["check_key"], "fee_lines_available")
        self.assertEqual(ev["payload"]["claimable_usd"], 0.0)

    def test_malformed_charge_is_uncertain_not_500(self):
        _, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee", amount="not-money")], [prep_record()]))
        row = out["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "UNCERTAIN")
        self.assertEqual(out["evidence"]["checks"][0]["verdict"], "UNCERTAIN")

    def test_internal_exception_fails_open_to_pending(self):
        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        with patch.object(round3, "assess_charges", side_effect=RuntimeError("boom")):
            code, out = run_round3(body)
        self.assertEqual(code, 200)
        self.assertEqual(out["status"], "pending")
        self.assertEqual(out["verdict"], "UNCERTAIN")
        self.assertEqual(out["evidence"]["decision"]["outcome"], "pending_review")
        self.assertTrue(out["evidence"]["decision"]["needs_human"])
        self.assertTrue(out["error"]["retryable"])
        self.assertEqual(out["evidence"]["checks"], [])
        self.assertEqual(out["evidence"]["upstream_refs"], ["PRP-T1"])
        self.assertTrue(verify_hash(out["evidence"]))

    def test_ai_summary_failure_keeps_assessment(self):
        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        body["context"]["include_ai_summary"] = True
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "ANTHROPIC_MODEL": "claude-test", "RECOVERY_ROUND3_AI": "auto"}),
            patch("recovery.round3_ai.claude_summary", side_effect=TimeoutError("SECRET_DETAIL")),
        ):
            _, out = run_round3(body)
        ev = out["evidence"]
        self.assertEqual(out["verdict"], "FAIL")
        self.assertEqual(ev["payload"]["ai_summary"]["status"], "failed")
        self.assertNotIn("SECRET_DETAIL", json.dumps(ev))
        self.assertEqual(ev["model"]["name"], "claude")
        self.assertEqual(ev["model"]["calls"], 1)

    def test_no_anthropic_key_means_no_model_call(self):
        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        body["context"]["include_ai_summary"] = True
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-be-used"}, clear=False),
            patch("recovery.round3_ai.claude_summary") as summary,
        ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            _, out = run_round3(body)
        summary.assert_not_called()
        self.assertEqual(out["evidence"]["model"], {"name": "rules", "version": round3.RULE_VERSION,
                                                     "provider": None, "prompt_version": None, "calls": 0, "cost_usd": 0})

    # -- tenancy and validation ---------------------------------------------

    def test_unknown_tenant_is_refused(self):
        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        body["subject"]["org_id"] = "org_other"
        code, out = run_round3(body)
        self.assertEqual(code, 404)
        self.assertEqual(out["error"], "unknown_subject")

    def test_other_org_evidence_and_lines_are_ignored(self):
        foreign_prep = prep_record(org="org_demo_bravo")
        foreign_line = charge("FEE-B-1", "inbound_defect_fee", org_id="org_demo_bravo")
        _, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee"), foreign_line], [foreign_prep]))
        ev = out["evidence"]
        self.assertEqual(ev["upstream_refs"], [])
        self.assertEqual(ev["payload"]["ignored_foreign_evidence"], ["PRP-T1"])
        self.assertEqual(ev["payload"]["dropped_foreign_lines"], ["FEE-B-1"])
        self.assertEqual([c["line_id"] for c in ev["payload"]["charges"]], ["FEE-T-1"])
        self.assertNotEqual(ev["payload"]["charges"][0]["position"], "CONTRADICTS")

    def test_unknown_subject_with_nothing_to_read_is_404(self):
        body = agent_input([], [])
        body["subject"]["subject_id"] = "UNIT-DOES-NOT-EXIST"
        code, _ = run_round3(body)
        self.assertEqual(code, 404)

    def test_invalid_input_is_422(self):
        code, out = run_round3({"request_id": "x"})
        self.assertEqual(code, 422)
        self.assertEqual(out["error"], "invalid_agent_input")
        code, _ = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], stage="prep"))
        self.assertEqual(code, 422)
        with self.assertRaises(round3.InputProblem):
            handle([])

    def test_sample_csv_fallback_for_demo_unit(self):
        body = agent_input([], [])
        body["context"].pop("charges")
        body["subject"]["subject_id"] = "UNIT-0014"
        body["workflow_id"] = "WF-org_demo_alpha-UNIT-0014"
        _, out = run_round3(body)
        ev = out["evidence"]
        self.assertEqual(ev["payload"]["charge_source"], "sample_csv")
        self.assertEqual(len(ev["payload"]["charges"]), 4)
        self.assertEqual(ev["payload"]["claimable_usd"], 0.0, "no upstream evidence: nothing may be claimed")
        self.assertTrue(all(i["kind"] == "csv_row" and len(i["sha256"]) == 64 for i in ev["inputs"]))

    def test_pack_pass_contradicts_generic_defect_when_prep_is_absent(self):
        pack = record(
            "pack", "PCK-T1", verdict="PASS", outcome="seal",
            checks=[{"check_key": "identity", "verdict": "PASS"}, {"check_key": "quantity", "verdict": "PASS"}],
        )
        _, out = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [pack]))
        row = out["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "CONTRADICTS")
        self.assertEqual(row["evidence_record_ids"], ["PCK-T1"])
        self.assertIn("Pack does not check", row["reason"])

    def test_pack_fail_supports_generic_defect(self):
        pack = record(
            "pack", "PCK-T1", verdict="FAIL", outcome="stop_and_fix",
            checks=[{"check_key": "missing", "verdict": "FAIL"}],
        )
        row = run_round3(agent_input([charge("FEE-T-1", "inbound_defect_fee")], [pack]))[1]
        self.assertEqual(row["evidence"]["payload"]["charges"][0]["position"], "SUPPORTS")
        self.assertEqual(row["verdict"], "PASS")

    def test_pack_does_not_judge_a_named_prep_allegation(self):
        pack = record("pack", "PCK-T1", verdict="PASS", outcome="seal", checks=[{"check_key": "identity", "verdict": "PASS"}])
        row = run_round3(agent_input(
            [charge("FEE-T-1", "inbound_defect_fee", alleged_defect="polybag_not_sealed")], [pack],
        ))[1]["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "SILENT")

    def test_prep_and_pack_disagreement_is_uncertain(self):
        pack = record("pack", "PCK-T1", verdict="FAIL", outcome="stop_and_fix", checks=[{"check_key": "missing", "verdict": "FAIL"}])
        row = run_round3(agent_input(
            [charge("FEE-T-1", "inbound_defect_fee")], [prep_record(), pack],
        ))[1]["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "UNCERTAIN")
        self.assertIn("CONFLICTING_OBSERVATIONS", row["flags"])

    def test_returns_quantity_is_copied_when_present(self):
        ret = returns_record()
        ret["payload"] = {"returned_quantity": 1, "received_at": "2026-06-20T07:36:00Z"}
        row = run_round3(agent_input(
            [charge("FEE-T-4", "refund_issued_item_not_returned", amount="12.00")], [ret],
        ))[1]["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "CONTRADICTS")
        self.assertIn("returned_quantity 1", row["reason"])

    def test_lost_inbound_stays_silent_even_with_a_sealed_pack(self):
        pack = record("pack", "PCK-T1", verdict="PASS", outcome="seal", checks=[{"check_key": "identity", "verdict": "PASS"}])
        row = run_round3(agent_input(
            [charge("FEE-T-3", "lost_inbound", amount="8.00", report_type="inventory_adjustment")], [pack],
        ))[1]["evidence"]["payload"]["charges"][0]
        self.assertEqual(row["position"], "SILENT")
        self.assertIn("F-10", row["reason"])
        self.assertEqual(row["evidence_record_ids"], ["PCK-T1"])

    def test_same_input_is_served_from_cache_without_a_second_summary(self):
        stored = {}

        def persist(record_id, output):
            stored[record_id] = output

        def load(record_id, fingerprint):
            found = stored.get(record_id)
            if found and found["evidence"]["payload"].get("input_fingerprint") == fingerprint:
                return found
            return None

        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()], request_id="req-cache")
        with (
            patch.dict(os.environ, {"RECOVERY_ROUND3_CACHE": "on"}),
            patch.object(round3, "_persist", persist),
            patch.object(round3, "_load_cached", load),
            patch.object(
                round3,
                "maybe_summarize",
                return_value={"status": "complete", "called": True, "model": "claude-sonnet-4-5", "text": "once"},
            ) as summary,
        ):
            first = run_round3(deepcopy(body))[1]
            second = run_round3(deepcopy(body))[1]
        self.assertEqual(first["evidence"]["content_hash"], second["evidence"]["content_hash"])
        self.assertEqual(second["evidence"]["payload"]["ai_summary"]["text"], "once")
        summary.assert_called_once()

    def test_openai_and_gemini_keys_are_not_used(self):
        body = agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()])
        body["context"]["include_ai_summary"] = True
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "hers", "GEMINI_API_KEY": "hers"}, clear=False),
            patch("recovery.round3_ai.claude_summary") as summary,
        ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            _, out = run_round3(body)
        summary.assert_not_called()
        self.assertEqual(out["evidence"]["model"]["calls"], 0)

    def test_demo_cases_match_their_expected_outcomes(self):
        for case in round3.demo_cases():
            _, out = run_round3(case["body"])
            self.assertEqual(out["evidence"]["decision"]["outcome"], case["expect_outcome"], case["case_id"])
            self.assertFalse(out["evidence"]["payload"]["claim_filed"])


class Round3HttpTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(round3, "_persist", lambda record_id, output: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        env = patch.dict(os.environ, {"RECOVERY_ROUND3_CACHE": "off"})
        env.start()
        self.addCleanup(env.stop)

    def test_health_advertises_round3(self):
        data = self.client.get("/health").json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["stage"], "recovery")
        self.assertEqual(data["agent_id"], "recovery-manager@1.0.0")
        self.assertEqual(data["round3"]["run"], "/run")
        self.assertIsInstance(data["round3"]["anthropic_configured"], bool)

    def test_run_endpoint_needs_no_bearer_and_returns_envelope(self):
        response = self.client.post("/run", json=agent_input([charge("FEE-T-1", "inbound_defect_fee")], [prep_record()]))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stage"], "recovery")
        self.assertTrue(verify_hash(body["evidence"]))

    def test_run_endpoint_status_codes(self):
        self.assertEqual(self.client.post("/run", json={"request_id": "x"}).status_code, 422)
        self.assertEqual(self.client.post("/run", content=b"not json", headers={"content-type": "application/json"}).status_code, 422)
        bad_org = agent_input([charge("FEE-T-1", "inbound_defect_fee")])
        bad_org["subject"]["org_id"] = "org_other"
        self.assertEqual(self.client.post("/run", json=bad_org).status_code, 404)

    def test_run_endpoint_never_500s_on_internal_error(self):
        with patch.object(round3, "assess_charges", side_effect=RuntimeError("boom")):
            response = self.client.post("/run", json=agent_input([charge("FEE-T-1", "inbound_defect_fee")]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")

    def test_demo_cases_endpoint(self):
        response = self.client.get("/demo/cases")
        self.assertEqual(response.status_code, 200)
        cases = response.json()["cases"]
        self.assertEqual([c["case_id"] for c in cases], [
            "prep_contradicts_packaging_fee",
            "return_receipt_contradicts",
            "missing_evidence",
        ])
        self.assertTrue(all(c["outcome"] == c["expect_outcome"] for c in cases))
        self.assertTrue(all(c["claim_filed"] is False for c in cases))


if __name__ == "__main__":
    unittest.main(verbosity=2)
