import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@contextmanager
def connection(org_id: str):
    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=10,
    ) as conn:
        role = conn.execute(
            """
            SELECT rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
            """
        ).fetchone()

        if role["rolsuper"] or role["rolbypassrls"]:
            raise RuntimeError(
                "The database login must not bypass row-level security."
            )

        conn.execute(
            "SELECT set_config('app.org_id', %s, true)",
            (org_id,),
        )

        yield conn