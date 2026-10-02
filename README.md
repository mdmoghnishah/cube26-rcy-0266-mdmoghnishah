# CUBE Buildathon 2026 · Recovery Manager

Recovery Manager helps seller operations teams review financial charges against operational evidence. It matches records, explains whether the evidence contradicts or supports an allegation, identifies missing information, and assembles conditional recovery review packets.

**Participant:** Mohammed Moghnishah  
**Track:** RCY · Recovery Manager  
**Repository:** [GitHub](https://github.com/mdmoghnishah/cube26-rcy-0266-mdmoghnishah)

## Project links

- **Live application:** [Recovery Manager](https://cube26-rcy-0266-mdmoghnishah.vercel.app/)
- **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Demo video:** [Google Drive](https://drive.google.com/drive/folders/1g4mpnY7lUL1tk6T_L2pIgOCrxGWAs8y5)
- **LinkedIn:** [Project post](https://www.linkedin.com/posts/contact-moghnishah_github-mdmoghnishahcube26-rcy-0266-mdmoghnishah-share-7511492023514914816-CPhs/)

## Problem understanding

Financial charges can appear weeks after the operational event that caused them. Sellers may struggle to locate the relevant inspection, shipment or return records when reviewing a charge.

A matching identifier alone does not establish that a charge is incorrect. A defensible assessment also needs relevant observations, consistent identity, suitable timing, applicable channel requirements and a justified financial basis.

Recovery Manager brings the charge, evidence, assessment, reconciliation and human review together.

It works with structured records. It does not perform camera capture or image analysis.

## Solution overview

The application supports:

- Importing the organiser’s synthetic reference CSVs.
- Uploading financial reports through the dashboard.
- Validating report fields before writing any rows.
- Enforcing organisation ownership using authenticated access tokens and PostgreSQL row-level security.
- Matching financial lines to relevant operational evidence.
- Checking identifier conflicts, specific packaging allegations and documented return receipts.
- Detecting duplicate charge and reimbursement references.
- Reconciling explicitly linked reimbursements.
- Calculating conditional potential amounts for supported fee scenarios.
- Generating AI summaries with one model request per unit-summary action.
- Preserving human reviews alongside original assessments.
- Exporting assessment JSON and recovery review packets.

This is a prototype for evidence review and potential claim preparation. It does not file claims or approve recoverable amounts.

## Assessment outcomes

| Outcome | Meaning |
| --- | --- |
| `CONTRADICTS` | Relevant structured evidence contradicts the reported allegation. |
| `SUPPORTS` | Relevant structured evidence supports the reported allegation. |
| `SILENT` | Available evidence does not address the allegation, or the line records a reimbursement rather than a new charge. |
| `UNCERTAIN` | Missing information, conflicts, ambiguous observations or unresolved reconciliation prevent a defensible conclusion. |

Assessments describe evidence. They do not establish marketplace eligibility or prove source authenticity.

Automatic `CONTRADICTS` and `SUPPORTS` outcomes are implemented for specific packaging scenarios. Return-receipt reasoning can produce `CONTRADICTS` when the required documented identity, recipient, quantity and timing agree.

## Technology stack

| Component | Technology |
| --- | --- |
| Frontend | Next.js App Router, React, CSS |
| API | Python, FastAPI |
| Database | Supabase PostgreSQL |
| Database access | Psycopg |
| Request validation | Pydantic |
| AI summaries | OpenAI Responses API |
| Configuration | Environment variables and python-dotenv |

## Project structure

```text
backend/
  recovery/
    db.py
    importer.py
    report_upload.py
    matcher.py
    decisions.py
    prep_rules.py
    shipment_rules.py
    return_rules.py
    reconciliation.py
    claims.py
    packets.py
    policies.py
    evaluation.py
    ai.py
    api.py
    seed_demo.py
    seed_reconciliation_demo.py
    seed_returns_demo.py
  tests/
    test_prep_rules.py
    test_shipment_rules.py
    test_return_rules.py
    test_reconciliation.py
    test_claims.py
    test_ai_failures.py
    test_report_upload.py
    test_all_table_isolation.py
  policy_snapshots/

frontend/
  app/
    page.js
    layout.js
    globals.css
  package.json

data/
  fee_report_sample.csv
  upstream/
    receiving_sample.csv
    prep_sample.csv
    pack_sample.csv
    returns_sample.csv

evaluation/
README.md
ARCHITECTURE.md
RULES.md
GITHUB-GUIDE.md
```

Environment files and credentials must remain outside version control.

## Prerequisites

- Python: local development was tested with Python 3.11. Use a version compatible with the backend deployment metadata; the deployment configuration targets Python 3.12 or later.
- Node.js and npm compatible with the installed Next.js version.
- A Supabase PostgreSQL database with the application schema provisioned.
- An OpenAI API key for AI summaries.

The database schema is a manual setup prerequisite. Installing Python packages does not create the tables or isolation policies.

## Backend setup

Run these commands from the repository root using PowerShell.

### 1. Create a virtual environment

```powershell
py -m venv backend\.venv
```

### 2. Install dependencies

```powershell
.\backend\.venv\Scripts\python.exe -m pip install fastapi "uvicorn[standard]" "psycopg[binary]" pydantic python-dotenv openai
```

### 3. Configure environment variables

Create `backend/.env`:

```dotenv
DATABASE_URL=postgresql://recovery_app.YOUR_PROJECT_REF:URL_ENCODED_PASSWORD@YOUR_SESSION_POOLER_HOST:5432/postgres?sslmode=require

ALPHA_TOKEN=YOUR_LONG_RANDOM_ALPHA_TOKEN
BRAVO_TOKEN=YOUR_DIFFERENT_LONG_RANDOM_BRAVO_TOKEN

FRONTEND_ORIGIN=http://localhost:3000

OPENAI_API_KEY=YOUR_OPENAI_API_KEY
OPENAI_MODEL=gpt-4.1-mini
```

Use distinct organisation tokens of at least 32 characters. Generate them with:

```powershell
.\backend\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(32))"
```

URL-encode special characters in the database password.

### 4. Provision the database

The `recovery` schema requires:

| Table | Purpose |
| --- | --- |
| `records` | Imported source rows, organisation ownership and content hashes |
| `decisions` | Saved assessment versions |
| `reviews` | Original decision snapshots and human reviews |

Required database controls:

- A restricted `recovery_app` role without superuser or RLS-bypass privileges.
- RLS enabled and forced on every application table.
- Organisation policies using `current_setting('app.org_id', true)`.
- A unique constraint on `(org_id, kind, content_hash)` in `records`.
- An organisation-scoped foreign key from reviews to decisions.
- Appropriate grants for reading, importing, saving decisions and recording reviews.

The backend sets the organisation context within each transaction and rejects connections using superuser or RLS-bypass roles.

### 5. Import reference data

```powershell
$env:PYTHONPATH = "backend"
.\backend\.venv\Scripts\python.exe -m recovery.importer
```

Identical imported rows are skipped using organisation, source kind and content hash.

### 6. Generate assessments

```powershell
.\backend\.venv\Scripts\python.exe -m recovery.decisions
```

### 7. Start the API

```powershell
.\backend\.venv\Scripts\python.exe -m uvicorn recovery.api:app --app-dir backend --reload
```

- API documentation: http://localhost:8000/docs
- Health endpoint: http://localhost:8000/health

## Frontend setup

Open a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:3000.

The frontend defaults to `http://localhost:8000`. To configure another API address, create `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Restart the frontend after changing environment variables. For deployment, configure the public API URL and set the backend’s `FRONTEND_ORIGIN` to the frontend origin.

## Usage

1. Enter an Alpha or Bravo organisation token and connect.
2. Upload a financial CSV, or use previously imported records.
3. Select a report line.
4. Inspect the assessment, explanation, evidence and reimbursement reconciliation.
5. Calculate a potential claim where supported.
6. Generate an optional AI unit summary.
7. Record a human assessment with a reason.
8. Download assessment JSON or a recovery review packet.

**Reassess records** creates new decision versions. Earlier versions remain in the database.

Human reviews belong to their selected decision version. They are not automatically transferred to later assessments.

## Financial report upload

The dashboard accepts UTF-8 CSV files with these required columns:

```csv
line_id,report_type,unit_id,charge_type,quantity,amount_usd,posted_date
```

Supported report types:

- `fee_report`
- `inventory_adjustment`
- `reimbursement_report`

Supported charge types:

- `inbound_defect_fee`
- `lost_inbound`
- `damaged_in_warehouse`
- `fulfilment_fee_weight_tier`
- `refund_issued_item_not_returned`

Validation includes:

- Maximum file size of 2 MB and 5,000 report rows.
- Required headers and nonempty required values.
- Valid report and charge types.
- Positive integer quantities.
- Nonnegative amounts with at most two decimal places.
- Valid dates in `YYYY-MM-DD` format.
- USD currency.
- Duplicate line IDs within the upload.
- Consistent CSV field counts and UTF-8 encoding.
- Organisation ownership matching the authenticated account.

The complete file is validated before database writes. Invalid files return row errors without importing rows.

Organisation ownership is assigned by the server. A conflicting `org_id` is rejected. Mixed-organisation files must be separated before upload.

Exact repeat records are skipped. Changed records with an existing line ID are not silently overwritten; duplicate handling requires review.

The API imports the report first. The frontend then requests assessment and refreshes the results. If assessment fails, the imported report remains saved.

The upload endpoint accepts CSV bytes directly as the request body rather than multipart form data.

## Evidence matching and reasoning

Matching uses organisation and `unit_id`, then selects evidence sources relevant to the charge type.

When available, the matcher checks SKU, order, FNSKU and shipment identifiers for conflicts.

Receiving, prep, pack and returns samples are stored. Current charge rules use relevant receiving, prep and returns records; pack-specific charge reasoning is not implemented.

### Packaging allegations

Specific packaging rules assess a documented `polybag_not_sealed` allegation.

Supported paths include:

- A matching inspection event and timestamp.
- An explicitly linked final inspection before dispatch, with documented continuity to dispatch.

Sealed observations can contradict the allegation; unsealed observations can support it. Conflicting observations, missing identifiers, pending inspections or unsuitable timing remain uncertain.

Generic defect fees do not become claims merely because a prep record exists.

### Return-related charges

Return reasoning requires:

- Matching organisation, unit, order and SKU.
- An explicit return-event reference.
- Confirmed identity and completed review state.
- Receipt by the required recipient.
- A documented return deadline.
- Valid receipt and capture timestamps.
- A returned quantity matching the charged quantity.

Partial quantity, late receipt, conflicting identity or the wrong recipient require review. An unrelated return event is silent on the charge.

These fields are development-adapter inputs. Their presence does not verify the deadline’s authority or establish real channel eligibility.

### Other charge types

- Weight-tier fees remain silent without measurements and an applicable fee schedule.
- Receiving or prep records alone do not establish channel loss.
- Condition observations alone do not establish responsibility for warehouse damage.

## Duplicate and reimbursement handling

Reconciliation checks duplicate report-line IDs and repeated source-transaction references.

Reimbursements are summed only when explicitly linked to the charge and consistent with the required identity and currency checks.

The implementation handles:

- Partial and full linked reimbursements.
- Duplicate payment lines or transaction references.
- Unallocated possible reimbursements.
- Invalid payment amounts.
- Over-reimbursement.
- Organisation separation.

A fully reimbursed charge receives `ALREADY_REIMBURSED` claim status. This does not establish whether the original allegation was correct.

A remaining reported balance is not an approved recoverable amount. No linked payment does not prove that the imported payment history is complete.

## Potential claims and review packets

For supported fee scenarios with contradicting cited evidence, the application calculates a conditional potential amount using:

- The reported fee, when no linked reimbursement is found; or
- The reported fee minus validated, explicitly linked reimbursements.

Duplicate flags, missing citations, inconsistent reconciliation and unsupported valuation prevent calculation.

Inventory-loss recovery requires a separate valuation basis and is not calculated from the current report amount.

Review packets include the report row, cited evidence, assessment, reconciliation, potential amount, human reviews, policy context and outstanding checks.

Approved amounts remain unset and submission readiness remains false.

Synthetic development scenarios are labelled as synthetic. A calculated demo amount does not establish an actual marketplace recovery.

## AI summaries

OpenAI summarises available report lines and matched evidence for a unit.

The integration:

- Batches the unit’s available records into one model request per summary action.
- Includes reconciliation information.
- Requests references to supplied report and evidence IDs.
- Instructs the model not to invent rules, measurements or photograph contents.
- Preserves deterministic assessments.
- Does not approve claims.
- Uses `store=False`.

End-to-end summary generation has been demonstrated in the dashboard. Summaries remain reviewer aids and require checking against source records.

Repeated requests can produce additional model calls.

### Failure handling

Pending state is saved before the model request. Timeout, provider error, empty output or incomplete output leaves the original records and assessments available.

Failures retain pending status and return a generic retry message without exposing provider exception details.

Background work runs in the API process. It is not a durable queue, and process interruption can leave work pending.

## Organisation isolation and reviews

Demo tokens map to `org_demo_alpha` and `org_demo_bravo`.

The backend derives organisation ownership from authentication. PostgreSQL RLS enforces the active organisation context.

All-table isolation checks passed for records, decisions and reviews, including cross-organisation reads and inserts, decision updates, ownership changes, review relationships and missing organisation context.

A cross-organisation request for an Alpha review packet using Bravo credentials returned 404.

Human reviews preserve:

- The original decision snapshot.
- The new assessment.
- A required reason.
- A reviewer identifier.
- A timestamp.

Reviews do not overwrite original assessments or approve claim amounts.

Demo-token authentication does not provide individual accounts, token expiry or production role management.

## Policy and evidence-contract status

Policy-source tracking records retrieval status and, when available, source content, retrieval time and a content hash.

The attempted Amazon bagging-policy retrieval remains `PENDING_SOURCE_REVIEW`. Applicable marketplace, event-date requirements and dispute eligibility remain unverified.

Tracking a source URL is not equivalent to verifying an applicable policy.

Compatibility with the organiser’s official cross-manager evidence contract has not been verified. The provided CSV samples and additional development fields are not presented as that contract.

## Reference and development data

The organiser supplied 276 synthetic reference rows:

| Source | Alpha | Bravo | Total |
| --- | ---: | ---: | ---: |
| Financial lines | 40 | 21 | 61 |
| Receiving | 67 | 33 | 100 |
| Prep | 41 | 21 | 62 |
| Pack | 20 | 9 | 29 |
| Returns | 11 | 13 | 24 |
| Total | 179 | 97 | 276 |

Additional synthetic development records demonstrate packaging outcomes, duplicate transactions, reimbursements and returns.

Optional demo commands:

```powershell
$env:PYTHONPATH = "backend"
.\backend\.venv\Scripts\python.exe -m recovery.seed_demo
.\backend\.venv\Scripts\python.exe -m recovery.seed_reconciliation_demo
.\backend\.venv\Scripts\python.exe -m recovery.seed_returns_demo
.\backend\.venv\Scripts\python.exe -m recovery.decisions
```

Sample identifiers, requirement flags, deadlines and monetary amounts are invented and are not authoritative marketplace rules.

Photo paths are references only. The application does not retrieve or verify the referenced images.

## Verification

The following tests passed during development:

| Test suite | Tests |
| --- | ---: |
| Packaging rules | 10 |
| Shipment timing and continuity | 12 |
| Return receipt rules | 16 |
| Reconciliation | 15 |
| Potential claim calculation | 11 |
| AI success and failure handling | 5 |
| CSV validation | 18 |
| Total | 87 |

Database isolation checks passed separately.

Run individual suites from the repository root, for example:

```powershell
$env:PYTHONPATH = "backend"
.\backend\.venv\Scripts\python.exe backend\tests\test_report_upload.py
.\backend\.venv\Scripts\python.exe backend\tests\test_return_rules.py
.\backend\.venv\Scripts\python.exe backend\tests\test_all_table_isolation.py
```

Dashboard checks demonstrated:

- Successful CSV import and assessment refresh.
- Exact-repeat skipping on reupload.
- Rejection of a conflicting organisation ID.
- Human-review preservation.
- AI summary generation.
- Partial reimbursement and conditional amount display.
- Recovery review packet export.

The observed local dataset after demo seeding and one upload contained:

| Organisation | Financial lines | SILENT | UNCERTAIN | CONTRADICTS | SUPPORTS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Alpha | 55 | 31 | 20 | 3 | 1 |
| Bravo | 21 | 17 | 4 | 0 | 0 |

These counts depend on imported data. They are output distributions, not accuracy measurements.

## Independent evaluation

An evaluation runner prepares cases and separate reviewer templates.

Independent evaluation on 50 unseen units labelled by two humans has not been completed. Claim precision, false positives, false negatives and human-label agreement are therefore not claimed.

Development tests, demo cases and the organiser’s unlabelled samples do not substitute for independent evaluation.

## Assumptions and limitations

- Real policy applicability and dispute eligibility remain unverified.
- Official evidence-contract compatibility remains unverified.
- Matching depends on documented identifiers and does not establish source authenticity or custody.
- Reasoning coverage is strongest for specific packaging and return-receipt scenarios.
- Current uploads accept supported CSV financial reports, not PDF or arbitrary document formats.
- Operational evidence is imported separately; the dashboard upload handles financial reports.
- Conditional potential amounts are not approved recoverable amounts.
- Claims are not filed automatically.
- Images are not uploaded, verified or served.
- Content hashes support repeat detection; they do not make records immutable or tamper-evident.
- AI summaries may contain mistakes.
- Background summary processing is not durable.
- Authentication uses demo organisation tokens.
- Independent evaluation remains incomplete.
- Local verification does not establish that every deployed environment behaves identically.

## Security and secrets

Never commit database passwords, API keys, organisation tokens or environment files containing credentials.

Use the restricted application database role.

If a credential is exposed, rotate or revoke it. Deleting it from a file does not invalidate the exposed credential.

## Author

Mohammed Moghnishah  
CUBE Buildathon 2026 · RCY Recovery Manager