from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from recovery.ai import summarize_unit


class FakeDatabase:
    def __init__(self):
        self.ids = [uuid4(), uuid4()]
        self.rows = {}

        for index, decision_id in enumerate(self.ids, start=1):
            self.rows[decision_id] = {
                "id": decision_id,
                "result": {
                    "org_id": "org_demo_alpha",
                    "unit_id": "TEST-UNIT",
                    "line_id": f"TEST-FEE-{index}",
                    "charge": {
                        "charge_type": "inbound_defect_fee",
                        "amount_usd": "2.00",
                    },
                    "assessment": "UNCERTAIN",
                    "reason": "Policy applicability is unverified.",
                    "claim_status": "REVIEW",
                    "claim_amount_usd": None,
                    "evidence": [{
                        "manager": "prep",
                        "record_id": "TEST-PREP",
                        "raw": {
                            "polybag_present_sealed": "sealed",
                        },
                    }],
                },
            }

        self.original = deepcopy(self.rows)
        self.completed_transactions = 0
        self.organisations = []

    @contextmanager
    def connection(self, org_id):
        self.organisations.append(org_id)
        yield self
        self.completed_transactions += 1

    def execute(self, sql, params):
        if "SELECT DISTINCT ON" in sql:
            return SimpleNamespace(
                fetchall=lambda: deepcopy(list(self.rows.values()))
            )

        if "SELECT id, result" in sql:
            return SimpleNamespace(
                fetchone=lambda: deepcopy(
                    self.rows.get(params[0])
                )
            )

        if "UPDATE recovery.decisions" in sql:
            update, decision_id = params
            self.rows[decision_id]["result"].update(
                deepcopy(update.obj)
            )
            return SimpleNamespace(rowcount=1)

        raise AssertionError(f"Unexpected SQL: {sql}")


class AiProcessingTests(unittest.TestCase):
    def run_model(self, response=None, failure=None):
        database = FakeDatabase()
        client = MagicMock()
        client.__enter__.return_value = client

        def model_call(**kwargs):
            # Confirm pending state was committed before
            # the external model request.
            self.assertEqual(
                database.completed_transactions, 1
            )

            for row in database.rows.values():
                self.assertEqual(
                    row["result"]["ai_status"], "pending"
                )

            if failure is not None:
                raise failure

            return response

        client.responses.create.side_effect = model_call

        with (
            patch(
                "recovery.ai.connection",
                side_effect=database.connection,
            ),
            patch(
                "recovery.ai.OpenAI",
                return_value=client,
            ) as constructor,
            patch.dict(
                "os.environ",
                {"OPENAI_MODEL": "test-model"},
            ),
        ):
            summarize_unit(
                "org_demo_alpha",
                database.ids[0],
            )

        constructor.assert_called_once_with(
            timeout=20,
            max_retries=0,
        )

        # Both financial lines for one unit use one model call.
        client.responses.create.assert_called_once()

        arguments = client.responses.create.call_args.kwargs
        self.assertFalse(arguments["store"])

        payload = json.loads(arguments["input"])
        self.assertEqual(len(payload["charges"]), 2)

        # Shared evidence appears once.
        self.assertEqual(len(payload["evidence"]), 1)

        self.assertEqual(
            database.organisations,
            ["org_demo_alpha", "org_demo_alpha"],
        )

        # Model processing may add AI metadata, but must not
        # change financial or evidence fields.
        for decision_id, original in database.original.items():
            actual = database.rows[decision_id]["result"]

            for key, value in original["result"].items():
                self.assertEqual(actual[key], value)

            self.assertIsNone(actual["claim_amount_usd"])

        return database

    def assert_pending(self, database):
        for row in database.rows.values():
            result = row["result"]

            self.assertEqual(result["ai_status"], "pending")
            self.assertIsNone(result["ai_summary"])
            self.assertIn(
                "Original records retained",
                result["ai_error"],
            )

    def test_timeout_preserves_records(self):
        database = self.run_model(
            failure=TimeoutError("Simulated timeout")
        )
        self.assert_pending(database)

    def test_provider_error_does_not_expose_sensitive_message(self):
        database = self.run_model(
            failure=RuntimeError(
                "Simulated provider error SECRET_TEST_VALUE"
            )
        )
        self.assert_pending(database)

        for row in database.rows.values():
            self.assertNotIn(
                "SECRET_TEST_VALUE",
                row["result"]["ai_error"],
            )

    def test_incomplete_response_stays_pending(self):
        database = self.run_model(
            response=SimpleNamespace(
                status="incomplete",
                output_text="Partial response",
            )
        )
        self.assert_pending(database)

    def test_empty_response_stays_pending(self):
        database = self.run_model(
            response=SimpleNamespace(
                status="completed",
                output_text="   ",
            )
        )
        self.assert_pending(database)

    def test_success_batches_unit_and_preserves_assessments(self):
        database = self.run_model(
            response=SimpleNamespace(
                status="completed",
                output_text="Evidence still requires review.",
            )
        )

        for row in database.rows.values():
            result = row["result"]

            self.assertEqual(result["ai_status"], "complete")
            self.assertEqual(
                result["ai_summary"],
                "Evidence still requires review.",
            )
            self.assertIsNone(result["ai_error"])
            self.assertEqual(result["ai_model"], "test-model")


if __name__ == "__main__":
    unittest.main(verbosity=2)