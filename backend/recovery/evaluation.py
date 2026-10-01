import argparse
import csv
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from recovery.claims import build_potential_claim
from recovery.decisions import classify


VERDICTS = {"CONTRADICTS", "SUPPORTS", "SILENT", "UNCERTAIN"}
CLAIM_LABELS = {"YES", "NO", "UNCERTAIN"}


def read_cases(path):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))

    if not isinstance(cases, list) or not cases:
        raise ValueError("Provide a nonempty list of evaluation cases.")

    keys = set()

    for case in cases:
        for field in (
            "org_id", "line_id", "unit_id",
            "charge_type", "charge", "evidence",
        ):
            if field not in case:
                raise ValueError(f"Case is missing {field}.")

        key = (case["org_id"], case["line_id"])

        if key in keys:
            raise ValueError(
                "Evaluation case keys must be unique. Test duplicate "
                "transactions using distinct line IDs."
            )

        keys.add(key)

    return cases


def prepare(cases_path, output_dir):
    cases = read_cases(cases_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    # Keep each organisation's reconciliation inputs separate.
    grouped = defaultdict(list)
    for case in cases:
        grouped[case["org_id"]].append(case)

    predictions = []

    for case in cases:
        result = classify(case, grouped[case["org_id"]])
        claim = build_potential_claim(result)

        predictions.append({
            "org_id": case["org_id"],
            "line_id": case["line_id"],
            "unit_id": case["unit_id"],
            "charge_type": case["charge_type"],
            "assessment": result["assessment"],
            "potential_claim": claim,
            "result": result,
        })

    prediction_path = destination / "predictions.json"

    # Do not silently overwrite an existing evaluation run.
    with prediction_path.open("x", encoding="utf-8") as handle:
        json.dump(predictions, handle, indent=2)

    fields = [
        "org_id", "line_id", "assessment",
        "claim_justified", "potential_amount_usd", "reason",
    ]

    for reviewer in ("reviewer_1", "reviewer_2"):
        path = destination / f"{reviewer}.csv"

        with path.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()

            for case in cases:
                writer.writerow({
                    "org_id": case["org_id"],
                    "line_id": case["line_id"],
                })

    units = {
        (case["org_id"], case["unit_id"])
        for case in cases
    }

    print(f"Prepared {len(cases)} financial lines / {len(units)} units.")
    print("Reviewer templates contain no agent predictions.")
    print("Unseen status must be independently documented.")
    print("Files saved in:", destination)


def read_labels(path):
    labels = {}

    with Path(path).open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        for row in csv.DictReader(handle):
            key = (row["org_id"], row["line_id"])

            if key in labels:
                raise ValueError(f"Duplicate reviewer label: {key}")

            if row["assessment"] not in VERDICTS:
                raise ValueError(f"Missing/invalid assessment: {key}")

            if row["claim_justified"] not in CLAIM_LABELS:
                raise ValueError(f"Missing/invalid claim label: {key}")

            if not row["reason"].strip():
                raise ValueError(f"Reviewer reason required: {key}")

            if row["claim_justified"] == "YES":
                amount = Decimal(row["potential_amount_usd"])

                if (
                    not amount.is_finite()
                    or amount <= 0
                    or amount.as_tuple().exponent < -2
                ):
                    raise ValueError(f"Invalid labelled amount: {key}")

            labels[key] = row

    return labels


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate(output_dir):
    directory = Path(output_dir)

    predictions = json.loads(
        (directory / "predictions.json").read_text(encoding="utf-8")
    )
    first = read_labels(directory / "reviewer_1.csv")
    second = read_labels(directory / "reviewer_2.csv")

    expected = {
        (item["org_id"], item["line_id"])
        for item in predictions
    }

    if set(first) != expected or set(second) != expected:
        raise ValueError("Both reviewers must label exactly the same cases.")

    disagreement = []
    assessment_agreement = 0
    claim_agreement = 0
    tp = fp = fn = tn = 0
    amount_checked = amount_correct = 0
    eligible_count = 0

    per_check = defaultdict(
        lambda: {
            verdict: {"TP": 0, "FP": 0, "FN": 0}
            for verdict in sorted(VERDICTS)
        }
    )

    for prediction in predictions:
        key = (prediction["org_id"], prediction["line_id"])
        a, b = first[key], second[key]

        same_assessment = a["assessment"] == b["assessment"]
        same_claim = a["claim_justified"] == b["claim_justified"]

        assessment_agreement += int(same_assessment)
        claim_agreement += int(same_claim)

        same_amount = (
            a["claim_justified"] != "YES"
            or (
                b["claim_justified"] == "YES"
                and Decimal(a["potential_amount_usd"])
                == Decimal(b["potential_amount_usd"])
            )
        )

        if not (same_assessment and same_claim and same_amount):
            disagreement.append({
                "org_id": key[0],
                "line_id": key[1],
                "reviewer_1": a,
                "reviewer_2": b,
            })

        if same_assessment:
            actual = prediction["assessment"]
            expected_verdict = a["assessment"]
            check = per_check[prediction["charge_type"]]

            for verdict in VERDICTS:
                check[verdict]["TP"] += int(
                    actual == verdict and expected_verdict == verdict
                )
                check[verdict]["FP"] += int(
                    actual == verdict and expected_verdict != verdict
                )
                check[verdict]["FN"] += int(
                    actual != verdict and expected_verdict == verdict
                )

        # Claim metrics exclude disagreements and uncertain labels.
        if (
            not same_claim
            or not same_amount
            or a["claim_justified"] == "UNCERTAIN"
        ):
            continue

        eligible_count += 1
        proposed = (
            prediction["potential_claim"]["potential_amount_usd"]
            is not None
        )
        justified = a["claim_justified"] == "YES"

        tp += int(proposed and justified)
        fp += int(proposed and not justified)
        fn += int(not proposed and justified)
        tn += int(not proposed and not justified)

        if proposed and justified:
            amount_checked += 1
            amount_correct += int(
                Decimal(
                    prediction["potential_claim"]["potential_amount_usd"]
                ) == Decimal(a["potential_amount_usd"])
            )

    report = {
        "financial_lines": len(predictions),
        "unique_units": len({
            (item["org_id"], item["unit_id"])
            for item in predictions
        }),
        "unseen_status": "MUST_BE_DOCUMENTED_SEPARATELY",
        "assessment_agreement": ratio(
            assessment_agreement, len(predictions)
        ),
        "claim_label_agreement": ratio(
            claim_agreement, len(predictions)
        ),
        "disagreements": disagreement,
        "claim_metric_cases": eligible_count,
        "claim_metric_excluded_cases": len(predictions) - eligible_count,
        "claim_TP": tp,
        "claim_FP": fp,
        "claim_FN": fn,
        "claim_TN": tn,
        "potential_claim_precision": ratio(tp, tp + fp),
        "potential_claim_recall": ratio(tp, tp + fn),
        "amount_cases_checked": amount_checked,
        "amount_exact_match_rate": ratio(
            amount_correct, amount_checked
        ),
        "per_charge_type_verdict_metrics": dict(per_check),
        "method": (
            "Potential-claim proposals evaluated against agreeing, "
            "non-uncertain human claim labels. Amount disagreements "
            "excluded. Verdict metrics use agreeing assessment labels. "
            "Null metrics mean the denominator is zero. "
            "This does not measure filed or approved claims."
        ),
    }

    path = directory / "metrics.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print("Metrics saved:", path)
    print("Potential claim precision:", report["potential_claim_precision"])
    print("Claim FP:", fp, "Claim FN:", fn)
    print("Reviewer disagreements:", len(disagreement))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "evaluate"])
    parser.add_argument("--cases")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.action == "prepare":
        if not args.cases:
            parser.error("--cases is required for prepare")
        prepare(args.cases, args.output)
    else:
        evaluate(args.output)