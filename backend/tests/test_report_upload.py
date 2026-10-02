import csv
import io
import unittest
from unittest.mock import patch

from recovery.report_upload import (
    MAX_BYTES,
    ReportValidationError,
    import_report,
    parse_report,
)


class ReportUploadTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "line_id": "UPLOAD-TEST-001",
            "report_type": "fee_report",
            "unit_id": "UPLOAD-UNIT-001",
            "charge_type": "inbound_defect_fee",
            "quantity": "1",
            "amount_usd": "10.00",
            "posted_date": "2026-06-20",
        }

    def csv_bytes(self, rows=None):
        rows = rows if rows is not None else [self.row]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=list(rows[0]),
        )
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")

    def assert_invalid(self, content, field):
        with self.assertRaises(ReportValidationError) as caught:
            parse_report(content, "org_demo_alpha")

        self.assertTrue(
            any(
                error["field"] == field
                for error in caught.exception.errors
            ),
            caught.exception.errors,
        )

    def test_valid_report_assigns_authenticated_org(self):
        rows = parse_report(
            self.csv_bytes(),
            "org_demo_alpha",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 2)
        self.assertEqual(
            rows[0][1]["org_id"],
            "org_demo_alpha",
        )
        self.assertEqual(rows[0][1]["currency"], "USD")
        self.assertEqual(
            rows[0][1]["dataset"],
            "uploaded-unverified",
        )

    def test_matching_org_is_accepted(self):
        self.row["org_id"] = "org_demo_alpha"

        rows = parse_report(
            self.csv_bytes(),
            "org_demo_alpha",
        )

        self.assertEqual(rows[0][1]["org_id"], "org_demo_alpha")

    def test_other_org_is_rejected(self):
        self.row["org_id"] = "org_demo_bravo"
        self.assert_invalid(self.csv_bytes(), "org_id")

    def test_missing_column_is_rejected(self):
        del self.row["unit_id"]
        self.assert_invalid(self.csv_bytes(), "columns")

    def test_empty_required_value_is_rejected(self):
        self.row["line_id"] = ""
        self.assert_invalid(self.csv_bytes(), "line_id")

    def test_duplicate_line_ids_are_rejected(self):
        self.assert_invalid(
            self.csv_bytes([self.row, dict(self.row)]),
            "line_id",
        )

    def test_invalid_amounts_are_rejected(self):
        for value in ("-1.00", "NaN", "Infinity", "1.001", "abc"):
            with self.subTest(value=value):
                self.row["amount_usd"] = value
                self.assert_invalid(self.csv_bytes(), "amount_usd")

    def test_invalid_quantities_are_rejected(self):
        for value in ("0", "-1", "1.5", "abc"):
            with self.subTest(value=value):
                self.row["quantity"] = value
                self.assert_invalid(self.csv_bytes(), "quantity")

    def test_invalid_date_is_rejected(self):
        self.row["posted_date"] = "2026-02-30"
        self.assert_invalid(self.csv_bytes(), "posted_date")

    def test_foreign_currency_is_rejected(self):
        self.row["currency"] = "INR"
        self.assert_invalid(self.csv_bytes(), "currency")

    def test_unknown_charge_type_is_rejected(self):
        self.row["charge_type"] = "unknown_fee"
        self.assert_invalid(self.csv_bytes(), "charge_type")

    def test_reimbursement_report_is_accepted(self):
        self.row["report_type"] = "reimbursement_report"

        rows = parse_report(
            self.csv_bytes(),
            "org_demo_alpha",
        )

        self.assertEqual(
            rows[0][1]["report_type"],
            "reimbursement_report",
        )

    def test_duplicate_headers_are_rejected(self):
        self.assert_invalid(
            b"line_id,line_id\nA,B\n",
            "columns",
        )

    def test_extra_fields_are_rejected(self):
        content = self.csv_bytes() + b"A,B,C,D,E,F,G,H\n"
        self.assert_invalid(content, "columns")

    def test_invalid_encoding_is_rejected(self):
        self.assert_invalid(b"\xff\xfe", "file")

    def test_empty_file_is_rejected(self):
        self.assert_invalid(b"", "file")

    def test_oversized_file_is_rejected(self):
        self.assert_invalid(b"x" * (MAX_BYTES + 1), "file")

    def test_invalid_upload_never_opens_database(self):
        self.row["amount_usd"] = "-10.00"

        with patch("recovery.report_upload.connection") as mocked:
            with self.assertRaises(ReportValidationError):
                import_report(
                    self.csv_bytes(),
                    "org_demo_alpha",
                )

            mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)