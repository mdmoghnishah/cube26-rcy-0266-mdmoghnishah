import os
import unittest
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


def isolate(decision_id):
    """Alpha can read its own decision. Bravo cannot read or update it."""
    with connection("org_demo_alpha") as conn:
        conn.execute(
            """
            INSERT INTO recovery.decisions (id, org_id, result)
            VALUES (%s, %s, %s)
            """,
            (
                decision_id,
                "org_demo_alpha",
                Jsonb({"purpose": "organization isolation test"}),
            ),
        )

    with connection("org_demo_alpha") as conn:
        row = conn.execute(
            "SELECT id FROM recovery.decisions WHERE id = %s",
            (decision_id,),
        ).fetchone()
        assert row is not None, "Alpha cannot read its own decision"

    with connection("org_demo_bravo") as conn:
        row = conn.execute(
            "SELECT id FROM recovery.decisions WHERE id = %s",
            (decision_id,),
        ).fetchone()
        assert row is None, "FAIL: Bravo can read Alpha's decision"

        updated = conn.execute(
            """
            UPDATE recovery.decisions
            SET result = %s
            WHERE id = %s
            """,
            (Jsonb({"unexpected": "change"}), decision_id),
        ).rowcount
        assert updated == 0, "FAIL: Bravo can update Alpha's decision"

    print("PASS: Alpha can read its decision.")
    print("PASS: Bravo cannot read or update Alpha's decision.")


@unittest.skipUnless(
    os.getenv("RECOVERY_RUN_DB_TESTS") == "1",
    "Set RECOVERY_RUN_DB_TESTS=1 to run the database isolation check",
)
class OrgIsolationTests(unittest.TestCase):
    def test_bravo_cannot_read_or_update_alpha(self):
        isolate(uuid4())


if __name__ == "__main__":
    unittest.main()
