from uuid import uuid4

from psycopg import errors
from psycopg.types.json import Jsonb

from recovery.db import connection


TABLES = ("records", "decisions", "reviews")
ALPHA = "org_demo_alpha"
BRAVO = "org_demo_bravo"


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def set_org(conn, org_id):
    conn.execute(
        "SELECT set_config('app.org_id', %s, true)",
        (org_id,),
    )


def insert_row(conn, table, org_id, row_id, decision_id=None):
    if table == "records":
        conn.execute(
            """
            INSERT INTO recovery.records (
                id, org_id, kind, raw, row_number, content_hash
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                row_id,
                org_id,
                "prep",
                Jsonb({
                    "org_id": org_id,
                    "unit_id": f"ISOLATION-{row_id}",
                    "record_id": str(row_id),
                }),
                1,
                uuid4().hex + uuid4().hex,
            ),
        )

    elif table == "decisions":
        conn.execute(
            """
            INSERT INTO recovery.decisions (id, org_id, result)
            VALUES (%s, %s, %s)
            """,
            (
                row_id,
                org_id,
                Jsonb({
                    "org_id": org_id,
                    "isolation_test": True,
                }),
            ),
        )

    elif table == "reviews":
        conn.execute(
            """
            INSERT INTO recovery.reviews (
                id, org_id, decision_id, original, review
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                row_id,
                org_id,
                decision_id,
                Jsonb({"isolation_test": True}),
                Jsonb({
                    "assessment": "UNCERTAIN",
                    "reason": "Temporary isolation test.",
                }),
            ),
        )

    else:
        raise ValueError("Unknown test table.")


def expect_denied(conn, action, message):
    try:
        # Nested transaction creates a savepoint, allowing
        # the outer test to continue after a rejected statement.
        with conn.transaction():
            action()
    except errors.InsufficientPrivilege:
        print(f"PASS: {message}")
    else:
        raise AssertionError(message + " — operation was allowed")


def visible(conn, table, row_id):
    # Table names come only from the fixed TABLES tuple.
    return conn.execute(
        f"SELECT id FROM recovery.{table} WHERE id = %s",
        (row_id,),
    ).fetchone()


def main():
    ids = {
        org: {table: uuid4() for table in TABLES}
        for org in (ALPHA, BRAVO)
    }

    with connection(ALPHA) as conn:
        try:
            role = conn.execute(
                """
                SELECT current_user AS name, rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            ).fetchone()

            check(
                not role["rolsuper"] and not role["rolbypassrls"],
                f"Role {role['name']} cannot bypass RLS.",
            )

            for table in TABLES:
                settings = conn.execute(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'recovery'
                      AND c.relname = %s
                      AND c.relkind = 'r'
                    """,
                    (table,),
                ).fetchone()

                check(
                    settings is not None
                    and settings["relrowsecurity"]
                    and settings["relforcerowsecurity"],
                    f"{table}: RLS enabled and forced.",
                )

            # Insert each organisation's rows under its own context.
            for org in (ALPHA, BRAVO):
                set_org(conn, org)

                for table in TABLES:
                    insert_row(
                        conn,
                        table,
                        org,
                        ids[org][table],
                        ids[org]["decisions"],
                    )

                    check(
                        visible(conn, table, ids[org][table])
                        is not None,
                        f"{org}: can insert and read own {table}.",
                    )

            # Check both directions.
            for current, other in ((ALPHA, BRAVO), (BRAVO, ALPHA)):
                set_org(conn, current)

                for table in TABLES:
                    check(
                        visible(conn, table, ids[other][table])
                        is None,
                        f"{current}: cannot read {other}'s {table}.",
                    )

                    expect_denied(
                        conn,
                        lambda table=table, other=other: insert_row(
                            conn,
                            table,
                            other,
                            uuid4(),
                            ids[other]["decisions"],
                        ),
                        f"{current}: cannot insert {other}'s {table}.",
                    )

                changed = conn.execute(
                    """
                    UPDATE recovery.decisions
                    SET result = result || %s
                    WHERE id = %s
                    """,
                    (
                        Jsonb({"unauthorised_change": True}),
                        ids[other]["decisions"],
                    ),
                ).rowcount

                check(
                    changed == 0,
                    f"{current}: cannot update {other}'s decision.",
                )

                expect_denied(
                    conn,
                    lambda current=current, other=other: conn.execute(
                        """
                        UPDATE recovery.decisions
                        SET org_id = %s
                        WHERE id = %s
                        """,
                        (other, ids[current]["decisions"]),
                    ),
                    f"{current}: cannot move a decision to {other}.",
                )

                # A review claiming the current org must not attach
                # to a decision belonging to the other org.
                try:
                    with conn.transaction():
                        insert_row(
                            conn,
                            "reviews",
                            current,
                            uuid4(),
                            ids[other]["decisions"],
                        )
                except errors.ForeignKeyViolation:
                    print(
                        f"PASS: {current}: cannot attach a review "
                        f"to {other}'s decision."
                    )
                else:
                    raise AssertionError(
                        "Cross-organisation review link was allowed."
                    )

            # Empty context must expose no application rows.
            set_org(conn, "")

            for table in TABLES:
                count = conn.execute(
                    f"SELECT count(*) AS count FROM recovery.{table}"
                ).fetchone()["count"]

                check(
                    count == 0,
                    f"No organisation context: zero {table} visible.",
                )

                expect_denied(
                    conn,
                    lambda table=table: insert_row(
                        conn,
                        table,
                        ALPHA,
                        uuid4(),
                        ids[ALPHA]["decisions"],
                    ),
                    f"No organisation context: {table} insert denied.",
                )

        finally:
            conn.rollback()
            print("Test transaction rolled back; no test rows retained.")

    print("ALL ISOLATION CHECKS PASSED")


if __name__ == "__main__":
    main()