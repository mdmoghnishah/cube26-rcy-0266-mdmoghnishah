import csv
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


ROOT = Path(__file__).resolve().parents[2]

FILES = {
    "fees": ROOT / "data" / "fee_report_sample.csv",
    "receiving": ROOT / "data" / "upstream" / "receiving_sample.csv",
    "prep": ROOT / "data" / "upstream" / "prep_sample.csv",
    "pack": ROOT / "data" / "upstream" / "pack_sample.csv",
    "returns": ROOT / "data" / "upstream" / "returns_sample.csv",
}


def load_rows(path: Path, kind: str):
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        identifier = "line_id" if kind == "fees" else "record_id"
        required = {"org_id", "unit_id", identifier}
        missing = required - set(reader.fieldnames or [])

        if missing:
            raise ValueError(
                f"{path.name}: missing columns {sorted(missing)}"
            )

        rows = []

        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(
                    f"{path.name}, row {row_number}: invalid CSV fields"
                )

            if any(not row[key].strip() for key in required):
                raise ValueError(
                    f"{path.name}, row {row_number}: missing identity"
                )

            rows.append((row_number, row))

        return rows


def import_samples(org_id: str):
    imported = 0
    unchanged = 0

    with connection(org_id) as conn:
        for kind, path in FILES.items():
            for row_number, row in load_rows(path, kind):
                if row["org_id"] != org_id:
                    continue

                serialized = json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                )

                content_hash = hashlib.sha256(
                    serialized.encode("utf-8")
                ).hexdigest()

                cursor = conn.execute(
                    """
                    INSERT INTO recovery.records (
                        id,
                        org_id,
                        kind,
                        raw,
                        row_number,
                        content_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (org_id, kind, content_hash)
                    DO NOTHING
                    """,
                    (
                        uuid4(),
                        org_id,
                        kind,
                        Jsonb(row),
                        row_number,
                        content_hash,
                    ),
                )

                if cursor.rowcount == 1:
                    imported += 1
                else:
                    unchanged += 1

    print(
        f"{org_id}: imported={imported}, "
        f"already_present={unchanged}"
    )


if __name__ == "__main__":
    # Local fixture import only. API organization identity will come
    # from authentication, rather than a user-provided organization ID.
    for organization in ("org_demo_alpha", "org_demo_bravo"):
        import_samples(organization)