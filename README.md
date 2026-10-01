# CUBE Buildathon 2026 · 05 · Recovery Manager

An evidence review application for seller operations teams. It matches financial report lines with receiving, prep, pack and returns records, explains what the available evidence establishes, and preserves human reviews.

**Participant:** Mohammed Moghnishah  
**Track:** RCY · Recovery Manager  
**Repository:** https://github.com/mdmoghnishah/cube26-rcy-0266-mdmoghnishah

## Problem understanding

Sellers need to understand whether operational evidence supports disputing a fee, inventory adjustment or reimbursement event.

A matching unit ID alone does not prove that a charge is incorrect. Evidence must address the specific charge, belong to the same organisation, and have consistent identifiers and relevant timing.

Recovery Manager brings the report line, matched evidence, assessment and review history together so an operator can inspect the reasoning.

## Solution overview

The application:

- Imports the provided synthetic CSV files into PostgreSQL.
- Matches financial lines with relevant operational records.
- Checks for identifier conflicts and missing information.
- Saves an assessment and explanation for each financial line.
- Displays the original report row and matched evidence.
- Records human reviews without replacing the original agent assessment.
- Exports the selected assessment and review history as JSON.
- Includes an OpenAI integration for evidence summaries grouped by unit.

The current implementation is an evidence review prototype. It does not submit recovery claims or establish approved recoverable amounts.

## Assessment outcomes

| Outcome | Meaning |
| --- | --- |
| `CONTRADICTS` | Relevant evidence contradicts the reported charge allegation. Further policy and monetary checks may still be required. |
| `SUPPORTS` | Relevant evidence supports the reported charge allegation. |
| `SILENT` | Available records do not address the charge, or the line is a reimbursement event recorded separately from a dispute. |
| `UNCERTAIN` | Evidence exists but missing information, conflicts, timing or unclear requirements prevent a defensible conclusion. |

Assessments describe evidence. They do not automatically determine claim eligibility.

The current sample run produces only `SILENT` and `UNCERTAIN` outcomes. Automatic `SUPPORTS` reasoning is not implemented. The interface accepts all four outcomes for human reviews.

## Technology stack

| Component | Technology |
| --- | --- |
| Frontend | Next.js App Router, React, CSS |
| Backend | Python, FastAPI |
| Database | Supabase PostgreSQL |
| Database access | Psycopg |
| Request validation | Pydantic |
| AI summaries | OpenAI Responses API |
| Configuration | Environment variables and python-dotenv |

LangChain is not required for the current implementation.

## Project structure

```text
backend/
  recovery/
    __init__.py
    db.py
    importer.py
    matcher.py
    decisions.py
    api.py
    ai.py
  tests/
    test_isolation.py
  .env

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

README.md
RULES.md
GITHUB-GUIDE.md
```

The backend `.env` file is local configuration and must not be committed.

## Prerequisites

- Python 3.11 or later.
- Node.js and npm compatible with the installed Next.js version.
- A Supabase PostgreSQL project.
- A configured `recovery` database schema with organisation isolation.
- An OpenAI API key to use AI summaries.

Run the following commands from the repository root unless stated otherwise.

## Backend setup

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

Use two distinct organisation tokens of at least 32 characters. Generate them with:

```powershell
.\backend\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(32))"
```

If the database password contains special characters, URL-encode it before placing it in the connection URI.

### 4. Database requirements

The application expects these tables in the `recovery` schema:

| Table | Purpose |
| --- | --- |
| `records` | Imported source rows and content hashes |
| `decisions` | Saved financial-line assessments |
| `reviews` | Original assessment snapshots and human review data |

Database provisioning is currently a manual prerequisite. The Python setup commands do not create the schema.

The database must have:

- A restricted application role named `recovery_app`.
- `org_id` on every application table.
- Row-level security enabled and forced on every application table.
- Policies scoped to `current_setting('app.org_id', true)`.
- An organisation-scoped relationship between reviews and decisions.
- No superuser or row-security bypass privileges for the application role.

The backend sets the organisation context within each database transaction. It rejects database connections using a role with superuser or row-security bypass privileges.

### 5. Import reference data

```powershell
$env:PYTHONPATH = "backend"
.\backend\.venv\Scripts\python.exe -m recovery.importer
```

Repeated imports skip identical rows using organisation, source kind and content hash.

### 6. Generate assessments

```powershell
.\backend\.venv\Scripts\python.exe -m recovery.decisions
```

### 7. Start the API

```powershell
.\backend\.venv\Scripts\python.exe -m uvicorn recovery.api:app --reload --host 127.0.0.1 --port 8000
```

API documentation:

http://localhost:8000/docs

Health endpoint:

http://localhost:8000/health

## Frontend setup

Open a second PowerShell terminal at the repository root:

```powershell
cd frontend
npm install
npm run dev
```

Open:

http://localhost:3000

The frontend uses `http://localhost:8000` as its default API URL.

If the frontend code uses `NEXT_PUBLIC_API_URL`, an alternative API address can be configured in `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Restart the frontend after changing environment variables.

## Usage

1. Enter an Alpha or Bravo organisation access token.
2. Click **Connect**.
3. Select a financial report line.
4. Inspect its original assessment, explanation and matched evidence.
5. Expand the evidence records to inspect the imported fields.
6. Optionally request an AI evidence summary.
7. Select the report line again after processing to refresh the summary.
8. Enter a human assessment and a reason.
9. Save the human review.
10. Download the assessment and review history as JSON.

**Reassess records** creates new assessment records. Earlier assessment versions remain in the database.

Human reviews belong to the selected decision version. A new assessment version does not automatically inherit reviews from an earlier version.

## Matching and decision logic

Matching uses the authenticated organisation and `unit_id`, then selects evidence sources relevant to the charge type.

Where identifiers are present, the matcher checks for conflicts involving SKU, order, FNSKU and shipment identifiers.

The assessment logic also checks for:

- Invalid financial values or quantities.
- Duplicate report-line identifiers.
- Potential related reimbursement entries.
- Missing evidence.
- Identifier conflicts.
- Missing measurement or policy information.
- Return-record timing and identity limitations.

Examples of conservative handling:

- A weight-tier fee remains `SILENT` without measured weight, dimensions and an applicable fee schedule.
- Receiving or prep records alone do not prove that an inbound unit was lost by the channel.
- A generic inbound defect fee remains `UNCERTAIN` when the specific alleged defect is unavailable.
- Related reimbursement entries require reconciliation before a recoverable balance can be established.

Potential duplicates and reimbursements are flagged for review. The application does not automatically allocate reimbursements or calculate a remaining claim balance.

## AI usage

OpenAI generates a narrative summary from the selected unit's available assessments and matched evidence.

The summary integration:

- Groups available checks into one request per unit-summary action.
- Uses existing report lines and evidence records as input.
- Instructs the model to reference supplied identifiers.
- Instructs the model not to invent policies, measurements or image observations.
- Does not replace the deterministic assessment.
- Does not approve claim amounts.
- Uses `store=False` for Responses API requests.

Repeated summary requests can create additional API calls.

The OpenAI connection test succeeded. End-to-end summary generation in the interface remains to be verified.

### Failure handling

A summary request marks processing as `pending` before the model call.

If the model request fails, the imported records and original assessment remain available. The summary stays pending and an error message indicates that it was not completed.

Background processing runs inside the API process. It is not a durable job queue, and a server restart can interrupt pending work.

## Organisation isolation

Demo access tokens map to:

- `org_demo_alpha`
- `org_demo_bravo`

The backend derives the organisation from the authenticated token. The client cannot choose another organisation through a request body.

PostgreSQL row-level security restricts access to the active organisation.

The current authentication mechanism is intended for demonstration. It does not provide individual user accounts, token expiry, account recovery or role management.

Human reviewers are identified as organisation demo-token holders.

## Human review history

Each saved review includes:

- The original decision snapshot.
- The reviewed assessment.
- A required explanation.
- A reviewer identifier.
- A creation timestamp.

Human reviews do not overwrite the original assessment and do not approve a claim amount.

## Reference data

All provided CSV rows are synthetic.

Sample SKUs, identifiers, requirement flags and monetary values are not authoritative marketplace rules or fees.

The imported dataset contains:

| Source | Alpha | Bravo | Total |
| --- | ---: | ---: | ---: |
| Financial lines | 40 | 21 | 61 |
| Receiving | 67 | 33 | 100 |
| Prep | 41 | 21 | 62 |
| Pack | 20 | 9 | 29 |
| Returns | 11 | 13 | 24 |
| **Total** | **179** | **97** | **276** |

Photo paths are sample references. The application does not verify, retrieve or display the referenced images as evidence.

## Verification and current results

### Organisation isolation test

```powershell
$env:PYTHONPATH = "backend"
.\backend\.venv\Scripts\python.exe backend\tests\test_isolation.py
```

Observed results:

- Alpha can read its own test decision.
- Bravo cannot read or update Alpha's test decision.

These checks cover decision access. They do not establish complete isolation coverage for every table or image-storage operation.

### Sample assessment run

| Organisation | Financial lines | SILENT | UNCERTAIN |
| --- | ---: | ---: | ---: |
| Alpha | 40 | 26 | 14 |
| Bravo | 21 | 17 | 4 |
| **Total** | **61** | **43** | **18** |

These are output counts, not accuracy measurements.

### Other observed checks

- Database connection reports the restricted `recovery_app` role.
- All 276 reference rows were imported.
- The frontend displays Alpha's 40 financial lines.
- A human review was saved while retaining the original assessment.
- A direct OpenAI API connection test returned successfully.

### Evaluation status

An independent evaluation on 50 unseen units with two human labellers has not been completed.

Claim precision, per-check false positives, false negatives and labeller agreement are therefore not reported. The sample records are not treated as labelled ground truth.

## Assumptions and limitations

- The integration currently follows the provided CSV shapes. Compatibility with the official cross-manager evidence contract has not been verified.
- Financial data enters through the command-line importer; a report-upload interface is not implemented.
- Matching depends on supplied identifiers and does not establish chain of custody.
- Authoritative marketplace requirements and fee schedules are not integrated.
- Sample requirement flags are not used as authoritative policy.
- Automatic `SUPPORTS` reasoning is not implemented.
- Recoverable amounts are not established.
- Claims are not filed automatically.
- Evidence images are not uploaded, verified or served.
- Content hashes help identify identical imported rows; they do not make records immutable or tamper-evident.
- AI summaries require human checking against original records.
- AI background tasks are not durable.
- The application uses demo organisation tokens rather than production user authentication.
- Human review does not establish evidence sufficiency or monetary eligibility.

## Security and secrets

Never commit API keys, database passwords, organisation tokens or `.env` files containing credentials.

Use the restricted database role for application requests.

If a credential is exposed, rotate or revoke it. Removing it from a file alone does not invalidate the exposed credential.

## Submission materials

- **GitHub:** https://github.com/mdmoghnishah/cube26-rcy-0266-mdmoghnishah
-  **Architecture documentation:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Demo video:** https://drive.google.com/drive/folders/1g4mpnY7lUL1tk6T_L2pIgOCrxGWAs8y5
- **Live deployment:** Not provided.
- **LinkedIn post:** To be added.

Update these entries with accessible final links before submitting.

## Author

Mohammed Moghnishah  
CUBE Buildathon 2026 · RCY Recovery Manager