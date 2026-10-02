import csv
import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from psycopg.types.json import Jsonb

from recovery.db import connection


MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_ERRORS = 50

REQUIRED_COLUMNS = {
    "line_id",
    "report_type",
    "unit_id",
    "charge_type",
    "quantity",
    "amount_usd",
    "posted_date",
}

REPORT_TYPES = {
    "fee_report",
    "inventory_adjustment",
    "reimbursement_report",
}

CHARGE_TYPES = {
    "inbound_defect_fee",
    "lost_inbound",
    "damaged_in_warehouse",
    "fulfilment_fee_weight_tier",
    "refund_issued_item_not_returned",
}


class ReportValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Report validation failed.")


def parse_report(content: bytes, org_id: str):
    errors = []
    rows = []
    seen_lines = set()

    def error(row_number, field, message):
        if len(errors) < MAX_ERRORS:
            errors.append({
                "row": row_number,
                "field": field,
                "message": message,
            })

    if not content or len(content) > MAX_BYTES:
        raise ReportValidationError([{
            "row": None,
            "field": "file",
            "message": "Upload a nonempty CSV of at most 2 MB.",
        }])

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ReportValidationError([{
            "row": None,
            "field": "file",
            "message": "CSV must use UTF-8 encoding.",
        }]) from None

    if "\x00" in text:
        raise ReportValidationError([{
            "row": None,
            "field": "file",
            "message": "CSV contains invalid null characters.",
        }])

    try:
        reader = csv.DictReader(
            io.StringIO(text, newline=""),
            strict=True,
        )

        headers = reader.fieldnames

        if not headers:
            raise ReportValidationError([{
                "row": 1,
                "field": "columns",
                "message": "CSV headers are missing.",
            }])

        headers = [header.strip() for header in headers]

        if any(not header for header in headers):
            error(1, "columns", "Column names cannot be empty.")

        if len(headers) != len(set(headers)):
            error(1, "columns", "Duplicate column names.")

        missing = REQUIRED_COLUMNS - set(headers)

        if missing:
            error(
                1,
                "columns",
                "Missing columns: " + ", ".join(sorted(missing)),
            )

        if errors:
            raise ReportValidationError(errors)

        reader.fieldnames = headers

        for row_number, raw in enumerate(reader, start=2):
            if row_number - 1 > MAX_ROWS:
                error(
                    row_number,
                    "file",
                    f"Maximum {MAX_ROWS} report rows allowed.",
                )
                break

            if None in raw or any(value is None for value in raw.values()):
                error(
                    row_number,
                    "columns",
                    "Row has a different number of fields than the header.",
                )
                continue

            row = {
                key: value.strip()
                for key, value in raw.items()
            }

            for field in sorted(REQUIRED_COLUMNS):
                if not row[field]:
                    error(row_number, field, "Value is required.")

            supplied_org = row.get("org_id", "")

            if supplied_org and supplied_org != org_id:
                error(
                    row_number,
                    "org_id",
                    "Organisation does not match the authenticated account.",
                )

            # Ownership always comes from authentication.
            row["org_id"] = org_id

            if row["report_type"] not in REPORT_TYPES:
                error(
                    row_number,
                    "report_type",
                    "Unsupported report type.",
                )

            if row["charge_type"] not in CHARGE_TYPES:
                error(
                    row_number,
                    "charge_type",
                    "Unsupported charge type.",
                )

            if not re.fullmatch(r"[1-9][0-9]*", row["quantity"]):
                error(
                    row_number,
                    "quantity",
                    "Quantity must be a positive whole number.",
                )

            try:
                amount = Decimal(row["amount_usd"])

                if (
                    not amount.is_finite()
                    or amount < 0
                    or amount.as_tuple().exponent < -2
                ):
                    raise ValueError()

            except (InvalidOperation, ValueError):
                error(
                    row_number,
                    "amount_usd",
                    "Use a nonnegative USD amount with at most two decimals.",
                )

            try:
                if not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}",
                    row["posted_date"],
                ):
                    raise ValueError()

                date.fromisoformat(row["posted_date"])

            except ValueError:
                error(
                    row_number,
                    "posted_date",
                    "Use a valid date in YYYY-MM-DD format.",
                )

            if row.get("currency") not in {None, "", "USD"}:
                error(
                    row_number,
                    "currency",
                    "This importer supports USD reports only.",
                )

            row["currency"] = "USD"

            line_id = row["line_id"]

            if line_id in seen_lines:
                error(
                    row_number,
                    "line_id",
                    "Repeated line ID in this upload.",
                )

            seen_lines.add(line_id)

            # Do not label uploaded records as verified or synthetic
            # without explicit provenance.
            if not row.get("dataset"):
                row["dataset"] = "uploaded-unverified"

            rows.append((row_number, row))

    except csv.Error:
        error(None, "file", "Malformed CSV or an oversized field.")

    if not rows and not errors:
        error(None, "file", "CSV contains no report rows.")

    if errors:
        raise ReportValidationError(errors)

    return rows


def import_report(content: bytes, org_id: str):
    # Validate the whole file before opening a write transaction.
    rows = parse_report(content, org_id)

    inserted = 0
    already_present = 0

    with connection(org_id) as conn:
        for row_number, row in rows:
            canonical = json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )

            content_hash = hashlib.sha256(
                canonical.encode("utf-8")
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
                    "fees",
                    Jsonb(row),
                    row_number,
                    content_hash,
                ),
            )

            if cursor.rowcount == 1:
                inserted += 1
            else:
                already_present += 1

    return {
        "org_id": org_id,
        "rows": len(rows),
        "inserted": inserted,
        "already_present": already_present,
    }