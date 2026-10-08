# Round 3 adapter (Recovery)

The Round 2 Recovery Manager stays as it is. Round 3 integration is a thin HTTP
boundary in `backend/recovery/round3.py`, wired into the existing FastAPI app.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/run` | CUBE Agent Input in, Agent Output (Evidence Record v1.0) out |
| `GET` | `/health` | Advertises `stage`, `agent_id`, `contract_version` and `round3.run` |

`/run` needs no bearer token (the orchestrator is trusted). It never touches the
database and never returns HTTP 500:

| Situation | Response |
| --- | --- |
| Malformed Agent Input | `422` |
| `subject.org_id` not a demo org, or unknown subject with nothing to read | `404` |
| Rules ran | `200`, `status: completed` |
| Any internal exception | `200`, `status: pending`, `verdict: UNCERTAIN`, `outcome: pending_review`, `error.retryable: true` |

Every output is also written to `data/round3-evidence/<record_id>.json` (gitignored).

## Run

```sh
PYTHONPATH=backend backend/.venv/bin/uvicorn recovery.api:app --port 8105
curl localhost:8105/health
```

Pod `agents/recovery/agent.json`:

```json
{
  "stage": "recovery",
  "agent_id": "recovery-manager@1.0.0",
  "owner": "@mdmoghnishah",
  "mode": "http",
  "url": "http://localhost:8105",
  "implementation": "Round 2 Python rules (CONTRADICTS/SUPPORTS/SILENT/UNCERTAIN) + Round 3 upstream-verdict adapter; optional Claude summary; no DB on /run"
}
```

## Input mapping

| Round 3 field | Recovery use |
| --- | --- |
| `subject.org_id`, `subject.subject_id` | organisation and `unit_id` |
| `inputs[].row` (`kind: csv_row`), else `context.charges`, else `data/fee_report_sample.csv` filtered by unit and org | fee report lines |
| `previous_evidence[]` (same org and unit only) | normalised into the matcher shape the Round 2 rules read |
| `context.overrides[]` | workflow-level overrides; latest `new_verdict` wins |
| `context.include_ai_summary: false` | skips the optional Claude summary |

Evidence from another organisation or another unit is ignored and listed in
`payload.ignored_foreign_evidence`. Fee lines owned by another organisation are
dropped and listed in `payload.dropped_foreign_lines`.

## How a position is decided

1. `recovery.decisions.classify` runs first, unchanged: financial validation,
   reconciliation, duplicate detection, identifier conflicts, packaging and
   return-receipt rules.
2. Only when that returns a plain `UNCERTAIN` with no flags (the Round 2
   adapter fields such as `inspection_event_id` or `return_event_id` are absent
   from Round 3 records) does `upstream_verdict_rule` read the upstream Round 3
   verdicts:
   - `inbound_defect_fee`: completed Prep record, effective verdict after
     overrides. A named `alleged_defect` maps to one Prep check
     (`polybag_not_sealed` -> `polybag_sealed`, and so on); PASS contradicts,
     FAIL supports, anything else is SILENT. With no named defect, only a Prep
     record that passed every recorded check contradicts the fee.
   - `refund_issued_item_not_returned`: completed Returns record with
     `identity_match` PASS contradicts; FAIL supports; pending is SILENT.
   - `lost_inbound`: always SILENT (finding F-10), including when a Pack record shows a sealed carton. A seal is not channel receipt.
   - `inbound_defect_fee` with no Prep record: a completed Pack record is read. Every pack check PASS contradicts a generic fee at pack time only, and the reason says Pack did not inspect polybag, suffocation, or FNSKU placement. A failed pack check supports the generic fee. A named prep allegation stays SILENT when only Pack is present. Prep and Pack on opposite sides of a generic fee is UNCERTAIN.
3. Returns payload fields `returned_quantity`, `received_at`, `receipt_status`, `received_by`, and `return_event_id` are copied when the Returns record actually contains them. They are not invented.
4. The same Agent Input (`input_fingerprint`) returns the stored output and does not call Claude again. Set `RECOVERY_ROUND3_CACHE=off` to force a fresh run.
5. `GET /demo/cases` runs three frozen stories with the model skipped: prep contradicts a packaging fee, a return receipt contradicts an item-not-returned fee, and a fee with no evidence stays unclaimed.
6. A contradicted line with amount `0.00` becomes SILENT (finding F-09).
7. `recovery.claims.build_potential_claim` decides whether a contradicted line
   can carry a conditional amount. Blocked lines stay in `payload.unclaimable`.

`payload.charges[].rule_path` records which module decided each line.

## Output mapping

| Recovery position | Check verdict | `uncertain_reason` |
| --- | --- | --- |
| `CONTRADICTS` | `FAIL` | - |
| `SUPPORTS` | `PASS` | - |
| `SILENT` | `UNCERTAIN` | `insufficient_evidence` |
| `UNCERTAIN` | `UNCERTAIN` | `conflicting_evidence`, `rule_unavailable` or `insufficient_evidence` |

| Decision | When |
| --- | --- |
| `FAIL` / `claim_recommended` / `needs_human: true` | any line has a conditional potential amount |
| `UNCERTAIN` / `insufficient_evidence` / `needs_human: true` | a line needs review or a contradiction is blocked |
| `UNCERTAIN` / `insufficient_evidence` / `needs_human: false` | only SILENT lines |
| `PASS` / `no_claim` | every line is supported |

`payload.claimable_usd` is a conditional potential amount. Nothing is filed and
nothing is approved.

## Model use

The assessment is decided by rules. When `ANTHROPIC_API_KEY` is set (and
`RECOVERY_ROUND3_AI` is not `off`), one Claude call per unit writes a reviewer
summary into `payload.ai_summary`. `ANTHROPIC_MODEL` defaults to
`claude-sonnet-4-5`. A failed summary is recorded as `status: failed` and the
assessment is unchanged. The OpenAI key used by the dashboard summary is not
read on this path.

## Content hash

SHA-256 of the canonical JSON (sorted keys, no whitespace) of the evidence
record excluding `content_hash` and `overrides`, matching
`shared/utils/hashing.py` in the Pod repo. It is a content hash, not tamper
evidence.

## Tests

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_round3.py
```
