from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


decision_id = uuid4()

# Save an Alpha test decision.
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

# Alpha must be able to read it.
with connection("org_demo_alpha") as conn:
    row = conn.execute(
        "SELECT id FROM recovery.decisions WHERE id = %s",
        (decision_id,),
    ).fetchone()

    assert row is not None, "Alpha cannot read its own decision"

# Bravo must not be able to read or update it.
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