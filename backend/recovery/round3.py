"""Round 3 adapter: CUBE Agent Input -> Agent Output for the Recovery stage.

Recovery is the last stage. It reads the Evidence Records produced upstream
(receiving / prep / pack / returns) plus the fee-report lines for the subject,
and emits one ``charge_<line_id>`` check per fee line.

Round 3 check semantics for Recovery (EVIDENCE-CONTRACT.md section 8):

    PASS       evidence SUPPORTS the charge      -> no claim
    FAIL       evidence CONTRADICTS the charge   -> potential claim, cite evidence
    UNCERTAIN  evidence SILENT / insufficient    -> never a claim, list in unclaimable

Design rules kept from Round 2:

* Python rules decide. ``recovery.decisions.classify`` runs first, unchanged.
* A model (Claude, optional) only writes a summary and never changes a verdict.
* Fail open: any exception produces a ``pending`` output, never an HTTP 500.
* No database. This module is pure and reads at most the sample fee CSV.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from recovery.claims import build_potential_claim
from recovery.decisions import classify
from recovery.matcher import RELEVANT_MANAGERS, identifier_conflicts

STAGE = "recovery"
AGENT_ID = "recovery-manager@1.0.0"
CONTRACT_VERSION = "1.0"
RULE_VERSION = "recovery-round3-adapter-0.1"
DEMO_ORGS = frozenset({"org_demo_alpha", "org_demo_bravo"})

REPO_ROOT = Path(__file__).resolve().parents[2]
FEE_SAMPLE = REPO_ROOT / "data" / "fee_report_sample.csv"
EVIDENCE_DIR = REPO_ROOT / "data" / "round3-evidence"

POSITION_TO_VERDICT = {
    "CONTRADICTS": "FAIL",
    "SUPPORTS": "PASS",
    "SILENT": "UNCERTAIN",
    "UNCERTAIN": "UNCERTAIN",
}

# Fee-line allegation -> Prep Round 3 check_key.
ALLEGATION_CHECKS = {
    "polybag_not_sealed": "polybag_sealed",
    "missing_suffocation_warning": "suffocation_warning",
    "fnsku_label_misplaced": "fnsku_label_placement",
    "barcode_not_covered": "original_barcode_covered",
    "expiry_not_legible": "expiry_legible",
    "missing_handling_marks": "handling_marks",
}

HASH_EXCLUDED = ("content_hash", "overrides")


class InputProblem(ValueError):
    """Malformed Agent Input (HTTP 422)."""


class UnknownTenant(LookupError):
    """Subject not under the requested organisation (HTTP 404)."""


# --------------------------------------------------------------------------
# Small helpers


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def content_hash(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k not in HASH_EXCLUDED}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def verify_hash(record: dict[str, Any]) -> bool:
    return record.get("content_hash") == content_hash(record)


def stable_record_id(request_id: str) -> str:
    return "RCY-" + hashlib.sha256(str(request_id).encode("utf-8")).hexdigest()[:12]


def check_key_for(line_id: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch == "_" else "_" for ch in str(line_id).lower()
    )
    return f"charge_{cleaned}"


def _money(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _financially_valid(charge: dict[str, Any]) -> bool:
    """Same gate as recovery.decisions.classify: amount, quantity and posting date parse."""
    amount = _money(charge.get("amount_usd"))
    if amount is None or amount < 0 or amount.as_tuple().exponent < -2:
        return False
    try:
        if int(str(charge.get("quantity"))) < 1:
            return False
        date.fromisoformat(str(charge.get("posted_date")))
    except (TypeError, ValueError):
        return False
    return charge.get("report_type") in {"fee_report", "inventory_adjustment"}


# --------------------------------------------------------------------------
# Input validation


def validate_input(body: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(body, dict):
        return ["body must be a JSON object"]
    if body.get("schema_version") not in (None, CONTRACT_VERSION):
        problems.append("schema_version must be '1.0'")
    for field in ("request_id", "workflow_id"):
        if not isinstance(body.get(field), str) or not body[field]:
            problems.append(f"{field} is required")
    if body.get("stage") not in (None, STAGE):
        problems.append(f"stage must be '{STAGE}'")
    subject = body.get("subject")
    if not isinstance(subject, dict):
        problems.append("subject is required")
    else:
        for field in ("org_id", "subject_id"):
            if not isinstance(subject.get(field), str) or not subject[field]:
                problems.append(f"subject.{field} is required")
    if body.get("previous_evidence") is not None and not isinstance(
        body["previous_evidence"], list
    ):
        problems.append("previous_evidence must be a list")
    if body.get("inputs") is not None and not isinstance(body["inputs"], list):
        problems.append("inputs must be a list")
    return problems


# --------------------------------------------------------------------------
# Upstream evidence normalisation


def overrides_for(record_id: str, overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in overrides
        if isinstance(row, dict)
        and (row.get("supersedes") or {}).get("record_id") == record_id
    ]


def effective_verdict(record: dict[str, Any], overrides: list[dict[str, Any]]) -> str:
    """Record verdict after workflow-level overrides; the latest override wins."""
    latest = overrides_for(record.get("record_id"), overrides)
    if latest:
        return latest[-1].get("new_verdict") or "UNCERTAIN"
    return (record.get("decision") or {}).get("verdict") or "UNCERTAIN"


def normalize_record(record: dict[str, Any], overrides: list[dict[str, Any]]) -> dict[str, Any]:
    """Map a Round 3 Evidence Record onto the matcher shape the Round 2 rules read.

    Only fields that mean the same thing are mapped. Nothing is invented:
    a Returns record without receipt fields stays without receipt fields,
    so the Round 2 return rule will say UNCERTAIN rather than guess.
    """
    stage = record.get("stage")
    subject = record.get("subject") or {}
    refs = subject.get("refs") or {}
    checks = {
        c.get("check_key"): c for c in record.get("checks") or [] if isinstance(c, dict)
    }
    decision = record.get("decision") or {}
    status = record.get("status")
    verdict = effective_verdict(record, overrides)

    raw: dict[str, Any] = {}
    raw.update({k: v for k, v in (record.get("payload") or {}).items()})
    raw.update(
        {
            "record_id": record.get("record_id"),
            "org_id": subject.get("org_id"),
            "unit_id": subject.get("unit_id") or subject.get("subject_id"),
            "captured_at": record.get("captured_at"),
            "processing_status": {
                "completed": "complete",
                "pending": "pending_review",
                "error": "pending_review",
            }.get(status, "pending_review"),
        }
    )
    for key in ("sku", "fnsku", "fba_shipment_id", "order_id", "asin", "work_order_id"):
        if refs.get(key) not in (None, ""):
            raw[key] = refs[key]

    if stage == "prep":
        raw["operator_verdict"] = verdict.lower()
        seal = checks.get("polybag_sealed")
        if seal:
            raw["polybag_present_sealed"] = {
                "PASS": "sealed",
                "FAIL": "not_sealed",
            }.get(seal.get("verdict"), "uncertain")
    elif stage == "returns":
        raw["ordered_sku"] = refs.get("sku")
        raw["operator_disposition"] = decision.get("outcome")
        identity = checks.get("identity_match")
        if identity:
            raw["identity_match"] = {"PASS": "yes", "FAIL": "no"}.get(
                identity.get("verdict"), "uncertain"
            )
        # Copy only receipt fields the Returns record actually carries.
        # Missing fields stay missing so the Round 2 return rule cannot guess.
        for key in (
            "return_event_id",
            "receipt_status",
            "received_by",
            "received_at",
            "returned_quantity",
        ):
            value = (record.get("payload") or {}).get(key)
            if value not in (None, ""):
                raw[key] = value
    elif stage == "pack":
        raw["operator_verdict"] = verdict.lower()

    return {
        "record_id": record.get("record_id"),
        "manager": stage,
        "captured_at": record.get("captured_at"),
        "identifier_conflicts": [],
        "raw": raw,
        "_round3": {
            "status": status,
            "verdict": verdict,
            "original_verdict": decision.get("verdict"),
            "outcome": decision.get("outcome"),
            "checks": {k: v.get("verdict") for k, v in checks.items()},
            "overridden": bool(overrides_for(record.get("record_id"), overrides)),
        },
    }


def split_evidence(
    previous: list[Any], org_id: str, subject_id: str, overrides: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (normalised in-scope records, ignored record_ids)."""
    kept: list[dict[str, Any]] = []
    ignored: list[str] = []
    for record in previous:
        if not isinstance(record, dict):
            continue
        subject = record.get("subject") or {}
        unit = subject.get("unit_id") or subject.get("subject_id")
        if subject.get("org_id") != org_id or unit != subject_id:
            ignored.append(str(record.get("record_id")))
            continue
        kept.append(normalize_record(record, overrides))
    return kept, ignored


# --------------------------------------------------------------------------
# Fee lines (charges)


def _normalise_charge(row: dict[str, Any]) -> dict[str, Any]:
    charge = {k: ("" if v is None else v) for k, v in row.items()}
    for key in ("quantity", "amount_usd"):
        if key in charge and not isinstance(charge[key], str):
            charge[key] = str(charge[key])
    return charge


def load_charges(body: dict[str, Any]) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Fee lines for this subject: inputs[].row, context.charges, or the sample CSV.

    Returns (charges, source, dropped_line_ids). Lines owned by another org are dropped.
    """
    subject = body["subject"]
    org_id, subject_id = subject["org_id"], subject["subject_id"]
    context = body.get("context") or {}

    candidates: list[dict[str, Any]] = []
    source = "none"

    for row in body.get("inputs") or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("row") or row.get("data") or row.get("fields")
        if isinstance(payload, dict) and payload.get("line_id"):
            candidates.append(dict(payload))
    if candidates:
        source = "inputs"
    else:
        for key in ("charges", "fee_lines", "report_lines"):
            rows = context.get(key)
            if isinstance(rows, list) and rows:
                candidates = [dict(r) for r in rows if isinstance(r, dict)]
                source = f"context.{key}"
                break
    if not candidates and FEE_SAMPLE.is_file():
        with FEE_SAMPLE.open(newline="", encoding="utf-8") as handle:
            candidates = [
                dict(r)
                for r in csv.DictReader(handle)
                if r.get("unit_id") == subject_id and r.get("org_id") == org_id
            ]
        source = "sample_csv" if candidates else "none"

    charges: list[dict[str, Any]] = []
    dropped: list[str] = []
    for row in candidates:
        charge = _normalise_charge(row)
        charge.setdefault("org_id", org_id)
        charge.setdefault("unit_id", subject_id)
        if charge["org_id"] != org_id or charge["unit_id"] != subject_id:
            dropped.append(str(charge.get("line_id")))
            continue
        charges.append(charge)
    return charges, source, dropped


# --------------------------------------------------------------------------
# Round 3 upstream-verdict rule (used only when Round 2 rules cannot decide)


def _latest(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    return sorted(items, key=lambda i: str(i.get("captured_at") or ""))[-1]


def _prep_inbound_position(preps, alleged, finish):
    if len({i["_round3"]["verdict"] for i in preps}) > 1:
        return finish(
            "UNCERTAIN",
            "Prep records for this unit disagree after overrides.",
            sorted(i["record_id"] for i in preps),
            ["CONFLICTING_OBSERVATIONS"],
        )
    prep = _latest(preps)
    meta = prep["_round3"]
    rid = prep["record_id"]
    if meta["status"] != "completed":
        return finish("SILENT", f"Prep record {rid} is {meta['status']}; not judged.", [rid])
    if meta["overridden"] and meta["verdict"] == "FAIL":
        return finish(
            "SUPPORTS",
            f"A workflow override marks Prep record {rid} FAIL; the defect allegation is supported.",
            [rid],
        )
    if alleged:
        key = ALLEGATION_CHECKS.get(alleged)
        check_verdict = meta["checks"].get(key) if key else None
        if check_verdict is None:
            return finish("SILENT", f"Prep record {rid} did not check '{alleged}'.", [rid])
        if check_verdict == "PASS":
            return finish(
                "CONTRADICTS",
                f"Prep check '{key}' PASS in {rid} contradicts '{alleged}'. "
                "Condition at prep time; later handling and policy applicability are not established.",
                [rid],
            )
        if check_verdict == "FAIL":
            return finish("SUPPORTS", f"Prep check '{key}' FAIL in {rid} supports '{alleged}'.", [rid])
        return finish("SILENT", f"Prep check '{key}' is UNCERTAIN in {rid}.", [rid])
    if meta["verdict"] == "PASS" and all(v == "PASS" for v in meta["checks"].values()) and meta["checks"]:
        return finish(
            "CONTRADICTS",
            f"Prep record {rid} passed every recorded prep check for this unit, contradicting a generic "
            "inbound defect fee. The fee names no specific defect; condition is established at prep time "
            "only, and channel policy applicability is unverified.",
            [rid],
        )
    if meta["verdict"] == "FAIL":
        return finish("SUPPORTS", f"Prep record {rid} reports a failed prep check.", [rid])
    return finish("SILENT", f"Prep record {rid} is UNCERTAIN; evidence does not settle the fee.", [rid])


def _pack_inbound_position(packs, alleged, finish):
    """Pack confirms what was sealed. It does not inspect prep-only allegations."""
    if len({i["_round3"]["verdict"] for i in packs}) > 1:
        return finish(
            "UNCERTAIN",
            "Pack records for this unit disagree.",
            sorted(i["record_id"] for i in packs),
            ["CONFLICTING_OBSERVATIONS"],
        )
    pack = _latest(packs)
    meta = pack["_round3"]
    rid = pack["record_id"]
    if alleged:
        return finish(
            "SILENT",
            f"Pack record {rid} does not inspect the named prep allegation '{alleged}'.",
            [rid],
        )
    if meta["status"] != "completed" or meta["verdict"] == "UNCERTAIN" or meta["outcome"] in {
        "review_required",
        "pending_review",
    }:
        return finish("SILENT", f"Pack record {rid} did not seal a decided result.", [rid])
    failed = [key for key, verdict in meta["checks"].items() if verdict == "FAIL"]
    if meta["verdict"] == "FAIL" or failed:
        named = ", ".join(failed) if failed else "a failed check"
        return finish(
            "SUPPORTS",
            f"Pack record {rid} failed {named} before seal, supporting a generic inbound defect. "
            "This is pack-time contents, not a channel inspection.",
            [rid],
        )
    if meta["verdict"] == "PASS" and meta["checks"] and all(v == "PASS" for v in meta["checks"].values()):
        return finish(
            "CONTRADICTS",
            f"Pack record {rid} passed every recorded pack check and the outcome is {meta['outcome']}. "
            "That contradicts a generic inbound defect fee at pack time only. Pack does not check "
            "polybag seal, suffocation warning, or FNSKU placement, and channel policy is unverified.",
            [rid],
        )
    return finish("SILENT", f"Pack record {rid} does not settle the fee.", [rid])


def upstream_verdict_rule(
    charge: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Position from upstream Round 3 verdicts when Round 2 adapter fields are absent.

    Conservative by construction: pending/error records, disagreeing records and
    unmapped allegations all stay SILENT. Timing and quantity caveats are stated.
    """
    charge_type = charge.get("charge_type")

    def finish(assessment: str, reason: str, ids: list[str] | None = None, flags=None):
        return {
            "assessment": assessment,
            "reason": reason,
            "claim_status": "REVIEW" if assessment == "CONTRADICTS" else "NOT_SUPPORTED",
            "claim_amount_usd": None,
            "supporting_evidence_ids": ids or [],
            "flags": flags or [],
        }

    if charge_type == "inbound_defect_fee":
        preps = [i for i in evidence if i.get("manager") == "prep" and i.get("_round3")]
        packs = [i for i in evidence if i.get("manager") == "pack" and i.get("_round3")]
        alleged = charge.get("alleged_defect")
        prep_pos = _prep_inbound_position(preps, alleged, finish) if preps else None
        pack_pos = _pack_inbound_position(packs, alleged, finish) if packs else None
        if (
            prep_pos
            and pack_pos
            and not alleged
            and {prep_pos["assessment"], pack_pos["assessment"]} == {"CONTRADICTS", "SUPPORTS"}
        ):
            return finish(
                "UNCERTAIN",
                "Prep and Pack disagree on this generic inbound defect fee.",
                sorted({*(prep_pos["supporting_evidence_ids"]), *(pack_pos["supporting_evidence_ids"])}),
                ["CONFLICTING_OBSERVATIONS"],
            )
        if pack_pos and not alleged and "CONFLICTING_OBSERVATIONS" in (pack_pos.get("flags") or []):
            return pack_pos
        if prep_pos and (alleged or prep_pos["assessment"] != "SILENT" or pack_pos is None):
            return prep_pos
        if pack_pos:
            return pack_pos
        return prep_pos

    if charge_type == "refund_issued_item_not_returned":
        returns = [i for i in evidence if i.get("manager") == "returns" and i.get("_round3")]
        if not returns:
            return None
        if len(returns) > 1:
            return finish(
                "UNCERTAIN",
                "Multiple Returns records for this unit; allocation to the charge is not established.",
                sorted(i["record_id"] for i in returns),
                ["CONFLICTING_OBSERVATIONS"],
            )
        ret = returns[0]
        meta = ret["_round3"]
        rid = ret["record_id"]
        if meta["status"] != "completed" or meta["outcome"] == "pending_review":
            return finish("SILENT", f"Returns record {rid} is pending review; not judged.", [rid])
        if meta["overridden"] and meta["verdict"] == "FAIL":
            return finish("SILENT", f"A workflow override disputes Returns record {rid}; no claim.", [rid])
        if charge.get("order_id") and ret["raw"].get("order_id") and charge["order_id"] != ret["raw"]["order_id"]:
            return finish("UNCERTAIN", f"Returns record {rid} is for a different order.", [rid], ["IDENTIFIER_CONFLICT"])
        identity = meta["checks"].get("identity_match")
        if identity == "PASS":
            qty = ret["raw"].get("returned_quantity")
            qty_note = (
                f" The record states returned_quantity {qty}."
                if qty not in (None, "")
                else " Returned quantity and return deadline are not stated by the Returns record."
            )
            return finish(
                "CONTRADICTS",
                f"Returns record {rid} confirms the ordered item was received back (identity_match PASS, "
                f"disposition {meta['outcome']}). This contradicts the item-not-returned allegation."
                + qty_note,
                [rid],
            )
        if identity == "FAIL":
            return finish("SUPPORTS", f"Returns record {rid} reports the returned item does not match the order.", [rid])
        return finish("SILENT", f"Returns record {rid} does not establish item identity.", [rid])

    if charge_type == "lost_inbound":
        # Finding F-10: a receiving shortfall is supplier-side; it is not proof for a channel loss claim.
        ids = sorted(
            i["record_id"]
            for i in evidence
            if i.get("manager") in {"receiving", "prep", "pack"} and i.get("record_id")
        )
        return finish(
            "SILENT",
            "Receiving, prep, and pack records describe supplier-side intake and seal, not channel "
            "receipt or loss (finding F-10). A sealed pack is not proof Amazon received the shipment. "
            "Evidence is silent on this charge; no claim.",
            ids,
        )

    return None


# --------------------------------------------------------------------------
# Assessment


def _uncertain_reason(result: dict[str, Any]) -> str:
    flags = set(result.get("flags") or [])
    reason = (result.get("reason") or "").lower()
    if flags & {"CONFLICTING_OBSERVATIONS", "IDENTIFIER_CONFLICT", "DUPLICATE_LINE_ID", "REPEATED_SOURCE_TRANSACTION"}:
        return "conflicting_evidence"
    if "identifiers conflict" in reason or "disagree" in reason:
        return "conflicting_evidence"
    if "no decision rule" in reason:
        return "rule_unavailable"
    return "insufficient_evidence"


def assess_charges(
    charges: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    matches = []
    for charge in charges:
        relevant_kinds = RELEVANT_MANAGERS.get(charge.get("charge_type"), set())
        relevant = []
        for item in evidence:
            if item.get("manager") not in relevant_kinds:
                continue
            copy = dict(item)
            copy["identifier_conflicts"] = identifier_conflicts(charge, item["raw"], item["manager"])
            relevant.append(copy)
        matches.append(
            {
                "line_id": charge.get("line_id"),
                "org_id": charge.get("org_id"),
                "unit_id": charge.get("unit_id"),
                "charge_type": charge.get("charge_type"),
                "charge": charge,
                "evidence": relevant,
            }
        )

    assessed = []
    for match in matches:
        result = classify(match, matches)
        rule_path = "round2_rules"

        # The Round 3 upstream rule only runs when Round 2 rules found nothing
        # wrong with the line itself and simply had no adapter fields to read.
        # It sees every in-scope record, including pack, which Round 2 does not
        # list as relevant for these charge types.
        nothing_for_round2 = (
            not match["evidence"]
            and result["assessment"] == "SILENT"
            and result.get("claim_status") == "NOT_SUPPORTED"
        )
        undecided = (
            _financially_valid(match["charge"])
            and not result.get("flags")
            and not (result.get("reconciliation") or {}).get("blocks_claim")
            and not any(i.get("identifier_conflicts") for i in match["evidence"])
            and (
                (result["assessment"] == "UNCERTAIN" and result.get("claim_status") == "REVIEW")
                or nothing_for_round2
            )
        )
        if undecided:
            upstream = upstream_verdict_rule(match["charge"], evidence)
            if upstream is not None:
                result.update(upstream)
                rule_path = "round3_upstream_verdict"

        amount = _money(match["charge"].get("amount_usd"))
        if result["assessment"] == "CONTRADICTS" and (amount is None or amount <= 0):
            result.update(
                assessment="SILENT",
                claim_status="NOT_SUPPORTED",
                reason="Amount is 0.00 or missing: nothing to claim (finding F-09). " + result["reason"],
            )

        result["rule_version"] = RULE_VERSION
        result["rule_path"] = rule_path
        # Strip adapter-private metadata before the result is persisted.
        for item in result.get("evidence", []):
            item.pop("_round3", None)
        result["potential_claim"] = build_potential_claim(result)
        assessed.append(result)
    return assessed


def charge_check(result: dict[str, Any]) -> dict[str, Any]:
    position = result["assessment"]
    verdict = POSITION_TO_VERDICT[position]
    check: dict[str, Any] = {
        "check_key": check_key_for(result["line_id"]),
        "verdict": verdict,
        "confidence": None,
        "expected": "charge supported by evidence",
        "observed": position,
        "detail": result.get("reason") or "",
        "evidence_refs": list(result.get("supporting_evidence_ids") or []),
    }
    if verdict == "UNCERTAIN":
        check["uncertain_reason"] = (
            "insufficient_evidence" if position == "SILENT" else _uncertain_reason(result)
        )
    return check


def charge_summary(result: dict[str, Any]) -> dict[str, Any]:
    claim = result.get("potential_claim") or {}
    amount = _money(result["charge"].get("amount_usd"))
    return {
        "line_id": result["line_id"],
        "charge_type": result.get("charge_type"),
        "report_type": result["charge"].get("report_type"),
        "amount_usd": float(amount) if amount is not None else None,
        "position": result["assessment"],
        "reason": result.get("reason"),
        "evidence_record_ids": list(result.get("supporting_evidence_ids") or []),
        "flags": list(result.get("flags") or []),
        "claim_status": result.get("claim_status"),
        "potential_claim_status": claim.get("status"),
        "potential_amount_usd": claim.get("potential_amount_usd"),
        "claim_block_reason": None if claim.get("potential_amount_usd") else claim.get("reason"),
        "reconciliation_status": (result.get("reconciliation") or {}).get("status"),
        "rule_path": result.get("rule_path"),
    }


def rollup(charges: list[dict[str, Any]], claimable: Decimal) -> tuple[str, str, bool, str]:
    """(verdict, outcome, needs_human, reason)."""
    positions = [c["position"] for c in charges]
    contradicted = sum(p == "CONTRADICTS" for p in positions)
    if not charges:
        return (
            "UNCERTAIN",
            "insufficient_evidence",
            False,
            "No fee report lines were supplied or found for this subject; nothing to assess.",
        )
    if claimable > 0:
        return (
            "FAIL",
            "claim_recommended",
            True,
            f"{len(charges)} charge(s); {contradicted} contradicted by cited evidence; "
            f"conditional potential claim {claimable:.2f} USD requires human review before filing.",
        )
    if "UNCERTAIN" in positions or (contradicted and claimable == 0):
        return (
            "UNCERTAIN",
            "insufficient_evidence",
            True,
            f"{len(charges)} charge(s); {positions.count('UNCERTAIN')} need review; "
            f"{contradicted} contradicted but blocked from claiming.",
        )
    if "SILENT" in positions:
        return (
            "UNCERTAIN",
            "insufficient_evidence",
            False,
            f"{len(charges)} charge(s); evidence is silent on {positions.count('SILENT')}; no claim.",
        )
    return (
        "PASS",
        "no_claim",
        False,
        f"{len(charges)} charge(s); evidence supports every charge; no claim.",
    )


# --------------------------------------------------------------------------
# Envelope


def _inputs_block(body: dict[str, Any], charges: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in body.get("inputs") or []:
        if not isinstance(row, dict) or not row.get("ref"):
            continue
        sha = row.get("sha256")
        if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
            sha = None
        kind = row.get("kind") if row.get("kind") in {"image", "video", "document", "csv_row", "record", "other"} else "csv_row"
        out.append({"ref": row["ref"], "sha256": sha, "kind": kind})
    if source == "sample_csv":
        for charge in charges:
            out.append(
                {
                    "ref": f"data/fee_report_sample.csv#{charge.get('line_id')}",
                    "sha256": hashlib.sha256(canonical_json(charge)).hexdigest(),
                    "kind": "csv_row",
                }
            )
    return out


def _captured_at(charges: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    stamps: list[str] = []
    for charge in charges:
        posted = charge.get("posted_date")
        try:
            date.fromisoformat(str(posted))
            stamps.append(f"{posted}T00:00:00Z")
        except (TypeError, ValueError):
            continue
    for item in evidence:
        if isinstance(item.get("captured_at"), str):
            stamps.append(item["captured_at"])
    return max(stamps) if stamps else utcnow()


def build_output(
    body: dict[str, Any],
    *,
    status: str,
    verdict: str,
    outcome: str,
    needs_human: bool,
    reason: str,
    checks: list[dict[str, Any]],
    payload: dict[str, Any],
    inputs: list[dict[str, Any]],
    upstream_refs: list[str],
    captured_at: str,
    model: dict[str, Any],
    latency_ms: int | None,
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    subject = body["subject"]
    produced_at = utcnow()
    evidence: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION,
        "record_id": stable_record_id(body["request_id"]),
        "workflow_id": body["workflow_id"],
        "stage": STAGE,
        "agent_id": AGENT_ID,
        "subject": {
            "org_id": subject["org_id"],
            "subject_id": subject["subject_id"],
            "unit_id": subject["subject_id"],
            "unit_scope": "unit",
            "refs": {},
        },
        "client_id": (body.get("context") or {}).get("client_id"),
        "status": status,
        "captured_at": captured_at,
        "produced_at": produced_at,
        "latency_ms": latency_ms,
        "operator_id": None,
        "model": model,
        "inputs": inputs,
        "checks": checks,
        "decision": {
            "verdict": verdict,
            "outcome": outcome,
            "confidence": None,
            "reason": reason,
            "needs_human": needs_human,
        },
        "payload": payload,
        "upstream_refs": upstream_refs,
        "overrides": [],
        "error": error,
    }
    evidence["content_hash"] = content_hash(evidence)
    output = {
        "schema_version": CONTRACT_VERSION,
        "workflow_id": body["workflow_id"],
        "stage": STAGE,
        "agent_id": AGENT_ID,
        "status": status,
        "verdict": verdict,
        "confidence": None,
        "timestamp": produced_at,
        "model": model,
        "error": error,
        "next_step_recommendation": {
            "action": "review" if needs_human else "complete",
            "reason": reason,
        },
        "evidence": evidence,
    }
    _persist(evidence["record_id"], output)
    return output


def pending_output(body: dict[str, Any], message: str, *, code: str = "agent_exception", retryable: bool = True) -> dict[str, Any]:
    """Fail-open output: a record exists, nothing was judged, a person must look."""
    previous = body.get("previous_evidence") or []
    return build_output(
        body,
        status="pending",
        verdict="UNCERTAIN",
        outcome="pending_review",
        needs_human=True,
        reason=message,
        checks=[],
        payload={"fail_open": True, "charges": [], "claimable_usd": 0.0, "unclaimable": []},
        inputs=_inputs_block(body, [], "none"),
        upstream_refs=[str(r.get("record_id")) for r in previous if isinstance(r, dict) and r.get("record_id")],
        captured_at=(body.get("context") or {}).get("captured_at") or utcnow(),
        model={"name": "none", "version": "0", "provider": None, "prompt_version": None, "calls": 0, "cost_usd": None},
        latency_ms=None,
        error={"code": code, "message": message, "retryable": retryable},
    )


def input_fingerprint(body: dict[str, Any]) -> str:
    material = {
        "schema_version": body.get("schema_version"),
        "request_id": body.get("request_id"),
        "workflow_id": body.get("workflow_id"),
        "stage": body.get("stage"),
        "subject": body.get("subject"),
        "inputs": body.get("inputs"),
        "previous_evidence": body.get("previous_evidence"),
        "context": body.get("context"),
    }
    return hashlib.sha256(canonical_json(material)).hexdigest()


def _evidence_path(record_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in record_id)
    return EVIDENCE_DIR / f"{safe}.json"


def _load_cached(record_id: str, fingerprint: str) -> dict[str, Any] | None:
    if os.getenv("RECOVERY_ROUND3_CACHE", "on").lower() == "off":
        return None
    path = _evidence_path(record_id)
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    evidence = stored.get("evidence") or {}
    if evidence.get("payload", {}).get("input_fingerprint") != fingerprint:
        return None
    if not verify_hash(evidence):
        return None
    return stored


def _persist(record_id: str, output: dict[str, Any]) -> None:
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        _evidence_path(record_id).write_text(
            json.dumps(output, indent=2, default=str), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        # Persistence is best effort; the orchestrator still receives the record.
        pass


# --------------------------------------------------------------------------
# Entry point


def handle(body: dict[str, Any]) -> dict[str, Any]:
    """Agent Input -> Agent Output. Raises InputProblem / UnknownTenant; otherwise never raises."""
    problems = validate_input(body)
    if problems:
        raise InputProblem("; ".join(problems))

    subject = body["subject"]
    org_id, subject_id = subject["org_id"], subject["subject_id"]
    if org_id not in DEMO_ORGS:
        raise UnknownTenant(f"unknown tenant {org_id}")

    started = time.monotonic()
    try:
        fingerprint = input_fingerprint(body)
        cached = _load_cached(stable_record_id(body["request_id"]), fingerprint)
        if cached is not None:
            return cached
        context = body.get("context") or {}
        overrides = [o for o in (context.get("overrides") or []) if isinstance(o, dict)]
        evidence, ignored = split_evidence(body.get("previous_evidence") or [], org_id, subject_id, overrides)
        charges, source, dropped = load_charges(body)

        if not charges and not evidence and source == "none":
            raise UnknownTenant(f"unknown subject {subject_id} in {org_id}")

        results = assess_charges(charges, evidence)
        summaries = [charge_summary(r) for r in results]
        claimable = sum(
            (Decimal(c["potential_amount_usd"]) for c in summaries if c["potential_amount_usd"]),
            Decimal("0.00"),
        )
        verdict, outcome, needs_human, reason = rollup(summaries, claimable)

        checks = [charge_check(r) for r in results]
        if not results:
            checks = [
                {
                    "check_key": "fee_lines_available",
                    "verdict": "UNCERTAIN",
                    "confidence": None,
                    "expected": "at least one fee report line for the subject",
                    "observed": 0,
                    "detail": reason,
                    "evidence_refs": [],
                    "uncertain_reason": "insufficient_evidence",
                }
            ]

        payload: dict[str, Any] = {
            "charges": summaries,
            "claimable_usd": float(claimable),
            "claimable_note": "Conditional potential amount from contradicted, unreimbursed fee lines. Not an approved recoverable amount; no claim is filed.",
            "unclaimable": [c for c in summaries if not c["potential_amount_usd"]],
            "positions": {p: sum(c["position"] == p for c in summaries) for p in POSITION_TO_VERDICT},
            "charge_source": source,
            "dropped_foreign_lines": dropped,
            "ignored_foreign_evidence": ignored,
            "upstream_agents_seen": sorted({i["manager"] for i in evidence if i.get("manager")}),
            "overrides_applied": sorted({(o.get("supersedes") or {}).get("record_id") for o in overrides if (o.get("supersedes") or {}).get("record_id")}),
            "rule_version": RULE_VERSION,
            "input_fingerprint": fingerprint,
            "assessment_decided_by": "rules",
            "claim_filed": False,
            "ai_summary": None,
        }

        model = {"name": "rules", "version": RULE_VERSION, "provider": None, "prompt_version": None, "calls": 0, "cost_usd": 0}
        summary = maybe_summarize(body, summaries, verdict, outcome, reason)
        if summary is not None:
            payload["ai_summary"] = summary
            if summary.get("called"):
                model = {
                    "name": "claude",
                    "version": summary.get("model") or "",
                    "provider": "anthropic",
                    "prompt_version": "recovery-summary-v1",
                    "calls": 1,
                    "cost_usd": None,
                }

        latency = int((time.monotonic() - started) * 1000)
        return build_output(
            body,
            status="completed",
            verdict=verdict,
            outcome=outcome,
            needs_human=needs_human,
            reason=reason,
            checks=checks,
            payload=payload,
            inputs=_inputs_block(body, charges, source),
            upstream_refs=[i["record_id"] for i in evidence if i.get("record_id")],
            captured_at=_captured_at(charges, evidence),
            model=model,
            latency_ms=latency,
            error=None,
        )
    except UnknownTenant:
        raise
    except Exception as exc:  # noqa: BLE001 - fail open for the orchestrator
        return pending_output(body, f"Recovery could not judge this subject: {type(exc).__name__}: {exc}")


def demo_cases() -> list[dict[str, Any]]:
    """Three frozen stories the stage can run without a CSV upload."""
    org = "org_demo_alpha"
    unit = "UNIT-DEMO"

    def body(request_id: str, charges: list[dict[str, Any]], previous: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": CONTRACT_VERSION,
            "request_id": request_id,
            "workflow_id": f"WF-{org}-{unit}",
            "stage": STAGE,
            "subject": {"org_id": org, "subject_id": unit, "route": "fba"},
            "inputs": [],
            "previous_evidence": previous,
            "context": {"charges": charges, "include_ai_summary": False},
        }

    def fee(line_id: str, charge_type: str, amount: str) -> dict[str, Any]:
        return {
            "line_id": line_id,
            "report_type": "fee_report",
            "unit_id": unit,
            "org_id": org,
            "sku": "SKU-DEMO",
            "charge_type": charge_type,
            "quantity": "1",
            "amount_usd": amount,
            "posted_date": "2026-07-18",
        }

    prep = {
        "schema_version": "1.0",
        "record_id": "PRP-DEMO",
        "workflow_id": f"WF-{org}-{unit}",
        "stage": "prep",
        "agent_id": "prep-manager@1.0.0",
        "subject": {"org_id": org, "subject_id": unit, "unit_id": unit, "refs": {"sku": "SKU-DEMO"}},
        "status": "completed",
        "captured_at": "2026-06-06T07:36:00Z",
        "model": {"name": "rules", "version": "0"},
        "inputs": [],
        "checks": [
            {"check_key": "polybag_sealed", "verdict": "PASS"},
            {"check_key": "suffocation_warning", "verdict": "PASS"},
        ],
        "decision": {"verdict": "PASS", "outcome": "ready", "reason": "demo", "needs_human": False},
        "payload": {},
        "overrides": [],
        "error": None,
    }
    returns = {
        "schema_version": "1.0",
        "record_id": "RTN-DEMO",
        "workflow_id": f"WF-{org}-{unit}",
        "stage": "returns",
        "agent_id": "returns-manager@1.0.0",
        "subject": {"org_id": org, "subject_id": unit, "unit_id": unit, "refs": {"sku": "SKU-DEMO", "order_id": "ORD-DEMO"}},
        "status": "completed",
        "captured_at": "2026-06-20T07:36:00Z",
        "model": {"name": "rules", "version": "0"},
        "inputs": [],
        "checks": [{"check_key": "identity_match", "verdict": "PASS"}],
        "decision": {"verdict": "PASS", "outcome": "restock", "reason": "demo", "needs_human": False},
        "payload": {"returned_quantity": 1},
        "overrides": [],
        "error": None,
    }
    return [
        {
            "case_id": "prep_contradicts_packaging_fee",
            "title": "Prep passed the seal and warning checks, so a generic inbound defect fee is contradicted.",
            "expect_outcome": "claim_recommended",
            "body": body("demo-prep", [fee("FEE-DEMO-1", "inbound_defect_fee", "2.50")], [prep]),
        },
        {
            "case_id": "return_receipt_contradicts",
            "title": "Returns confirmed the item came back, so an item-not-returned fee is contradicted.",
            "expect_outcome": "claim_recommended",
            "body": body("demo-return", [fee("FEE-DEMO-2", "refund_issued_item_not_returned", "12.00")], [returns]),
        },
        {
            "case_id": "missing_evidence",
            "title": "A fee line with no upstream record stays unclaimed.",
            "expect_outcome": "insufficient_evidence",
            "body": body("demo-missing", [fee("FEE-DEMO-3", "lost_inbound", "8.00")], []),
        },
    ]


def run_round3(body: Any) -> tuple[int, dict[str, Any]]:
    """HTTP-shaped wrapper: (status_code, json)."""
    try:
        return 200, handle(body)
    except InputProblem as exc:
        return 422, {"error": "invalid_agent_input", "detail": str(exc)}
    except UnknownTenant as exc:
        return 404, {"error": "unknown_subject", "detail": str(exc)}


# --------------------------------------------------------------------------
# Optional Claude summary (never changes a verdict)


def maybe_summarize(
    body: dict[str, Any],
    charges: list[dict[str, Any]],
    verdict: str,
    outcome: str,
    reason: str,
) -> dict[str, Any] | None:
    """One Claude call per unit, only when ANTHROPIC_API_KEY is set and not opted out.

    Uses only ANTHROPIC_API_KEY / ANTHROPIC_MODEL. OpenAI or Gemini keys are never read.
    A failure is recorded in the payload; the rule assessment is untouched.
    """
    context = body.get("context") or {}
    if context.get("include_ai_summary") is False or os.getenv("RECOVERY_ROUND3_AI", "auto").lower() == "off":
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    from recovery.round3_ai import claude_summary  # local import keeps the rules path dependency-free

    model = os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
    try:
        text = claude_summary(
            api_key=api_key,
            model=model,
            payload={
                "subject": body["subject"],
                "decision": {"verdict": verdict, "outcome": outcome, "reason": reason},
                "charges": charges,
            },
        )
        return {"status": "complete", "model": model, "called": True, "text": text, "note": "Reviewer aid only; rules decided the assessment."}
    except Exception:  # noqa: BLE001 - never expose provider errors, never change the verdict
        return {"status": "failed", "model": model, "called": True, "text": None, "note": "Summary failed or timed out; rule assessment retained."}
