# Recovery Manager Evaluation Report

**Author:** Mohammed Moghnishah  
**Track:** RCY · Recovery Manager  
**Report date:** 2 October 2026  
**Status:** Development verification completed; independent evaluation pending.

## 1. Scope

This report documents observed regression-test results, database isolation checks and manually demonstrated application workflows.

It distinguishes implementation verification from independent evaluation of claim correctness.

The required evaluation on 50 unseen units labelled independently by two humans has not been completed. This report does not claim to satisfy that requirement.

## 2. Data and methodology

Development used:

- Organiser-provided synthetic reference CSVs.
- Additional synthetic packaging, reimbursement and return scenarios.
- Test fixtures targeting specific rule boundaries and failure conditions.
- A synthetic CSV uploaded through the dashboard.

The organiser reference dataset contains 276 records, including 61 financial lines.

Synthetic monetary values, deadlines and requirement flags are not authoritative marketplace rules. The supplied samples do not contain independently established ground-truth labels.

Development fixtures were used while implementing the rules. They are not an unseen evaluation dataset.

Tests were run locally using the backend virtual environment. Results below are based on the observed terminal output and demonstrated application behaviour.

## 3. Regression-test results

| Suite | Test file | Passed |
| --- | --- | ---: |
| Packaging reasoning | `backend/tests/test_prep_rules.py` | 10 |
| Shipment timing and continuity | `backend/tests/test_shipment_rules.py` | 12 |
| Return receipt reasoning | `backend/tests/test_return_rules.py` | 16 |
| Duplicate and reimbursement handling | `backend/tests/test_reconciliation.py` | 15 |
| Conditional potential amounts | `backend/tests/test_claims.py` | 11 |
| AI processing and failures | `backend/tests/test_ai_failures.py` | 5 |
| CSV parsing and validation | `backend/tests/test_report_upload.py` | 18 |
| Total | | 87 |

Each listed suite completed successfully in its observed run.

The 87 tests were run across separate development steps. They are not presented as one consolidated test run against a recorded final commit.

Passing tests establish expected behaviour for the covered fixtures. They do not establish production accuracy, complete coverage or independent claim precision.

## 4. Packaging and shipment checks

Packaging tests covered:

- Sealed observations contradicting a specific unsealed-polybag allegation.
- Unsealed observations supporting that allegation.
- Conflicting observations producing uncertainty.
- Missing identifiers and pending inspections.
- Missing evidence and unrelated events.
- Invalid or unsuitable observation timing.
- Equivalent timezone representations.
- Generic charges remaining uncertain without sufficient allegation detail.

Shipment tests covered:

- Explicit linkage to a final inspection.
- Inspection before dispatch.
- Dispatch timing relative to posting.
- Required documented continuity.
- Missing, unavailable or duplicate record references.
- Organisation and shipment mismatches.
- Pending review states.

These checks verify structured-record reasoning. They do not authenticate inspections or prove actual chain of custody.

## 5. Return receipt checks

The 16 return tests covered:

- Complete matching receipt producing `CONTRADICTS`.
- No relevant records producing `SILENT`.
- Wrong return event producing `SILENT`.
- Organisation, order and SKU mismatches.
- Receipt at the wrong recipient.
- Unconfirmed receipt.
- Pending review.
- Receipt after the documented deadline.
- Partial returned quantity.
- Missing deadline.
- Missing timezone.
- Capture before receipt.
- Equivalent timezone representations.
- Duplicate relevant records.

A matching return observation does not automatically establish channel eligibility.

The rule uses explicitly supplied recipient and deadline fields. Their authority remains unverified.

## 6. Duplicate and reimbursement checks

The 15 reconciliation tests covered:

- Duplicate charge-line identifiers.
- Repeated charge source transactions.
- Duplicate reimbursement lines and payment transactions.
- Explicitly linked full and partial reimbursements.
- Multiple distinct payments.
- Invalid payment values.
- Missing currency.
- Wrong-unit payment linkage.
- Unallocated possible payments.
- Over-reimbursement.
- Organisation separation.
- No linked reimbursement without inventing a balance.

Full reimbursement blocks a new potential claim.

Partial reimbursement produces a reported arithmetic balance, not an approved recovery amount.

## 7. Potential amount checks

The 11 potential-claim tests covered:

- Conditional use of the reported fee when no linked payment is found.
- A $10 reported fee minus a $4 linked reimbursement producing a $6 potential amount.
- Blocking duplicate and fully reimbursed cases.
- Blocking missing cited evidence.
- Blocking assessment flags.
- Invalid amounts and foreign currency.
- Inconsistent reconciliation.
- Noncontradicting assessments.
- Inventory loss requiring separate valuation.

Approved claim amounts remain unset. Submission readiness remains false.

These tests verify calculation and blocking behaviour against supplied records. They do not establish actual recoverability.

## 8. AI failure handling

Five mocked tests covered:

- Successful batching of a unit’s assessments into one model call.
- Preservation of original assessments.
- Timeout.
- Provider error without exposing sensitive exception text.
- Empty or incomplete responses remaining pending.

Pending state is saved before the model request. Failures preserve imported records and original assessments.

End-to-end AI summary generation was also demonstrated manually in the dashboard.

The mocked tests do not measure summary factual accuracy or provider availability. Background tasks remain non-durable.

## 9. CSV validation

The 18 validation tests covered:

- Authenticated organisation assignment.
- Acceptance of matching organisation ownership.
- Rejection of conflicting organisation ownership.
- Missing columns and required values.
- Duplicate headers and line IDs.
- Invalid amounts, quantities and dates.
- Unsupported charge types and currencies.
- Reimbursement reports.
- Incorrect field counts.
- Invalid encoding.
- Empty and oversized files.
- Validation failure before opening a database connection.

Dashboard checks separately demonstrated successful import, exact-repeat skipping and display of organisation-validation errors.

## 10. Database and API isolation

The all-table isolation script passed checks for:

- A database role without superuser or RLS-bypass privileges.
- Enabled and forced RLS on `records`, `decisions` and `reviews`.
- Each organisation inserting and reading its own rows.
- Both organisations being unable to read or insert rows owned by the other.
- Cross-organisation decision updates being unavailable.
- Decision ownership changes being denied.
- Cross-organisation review relationships being denied.
- Zero visible rows without organisation context.
- Inserts being denied without organisation context.

The test transaction was rolled back, leaving no test rows retained.

A manual request for an Alpha review packet using Bravo authentication returned 404.

Image-access isolation was not evaluated because no evidence-image retrieval or serving workflow exists.

These results do not establish production authentication security or complete security coverage.

## 11. Demonstrated scenarios

| Scenario | Observed result |
| --- | --- |
| Sealed packaging at matching event | `CONTRADICTS` |
| Unsealed packaging at matching event | `SUPPORTS` |
| Ambiguous packaging observation | `UNCERTAIN` |
| Unrelated inspection event | `SILENT` |
| Repeated source charge transaction | `UNCERTAIN`, duplicate flag |
| $10 fee with $4 linked reimbursement | $6 remaining reported balance and conditional potential amount |
| $10 fee with $10 linked reimbursement | `ALREADY_REIMBURSED`, $0 reported balance |
| Complete matching return receipt | `CONTRADICTS` |
| Partial return quantity | `UNCERTAIN` |
| Wrong return event | `SILENT` |
| Wrong return recipient | `UNCERTAIN` |
| Upload with no matching operational evidence | `SILENT` |
| Repeated identical upload | 0 new rows; 1 exact repeat skipped |
| Bravo-owned row uploaded using Alpha authentication | Validation error; no rows imported |
| Human review | Original assessment retained |
| AI summary request | Completed summary displayed |
| Recovery review packet | JSON export returned |

The demonstration cases are synthetic and were used during development.

## 12. Observed output distribution

After development seeding and one synthetic upload:

| Organisation | Financial lines | SILENT | UNCERTAIN | CONTRADICTS | SUPPORTS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Alpha | 55 | 31 | 20 | 3 | 1 |
| Bravo | 21 | 17 | 4 | 0 | 0 |
| Total | 76 | 48 | 24 | 3 | 1 |

Counts change with imported data.

Financial lines are not unique evaluation units. These distributions are not accuracy measurements.

## 13. Independent evaluation status

| Required item | Status |
| --- | --- |
| 50 unseen units | Not completed |
| Two independent human labellers | Not completed |
| Documented label agreement | Not measured |
| Per-check false positives and false negatives | Not measured |
| Claim precision and recall | Not measured |
| Independently verified amount correctness | Not measured |
| Evaluation preparation tooling | Implemented; one-case preparation smoke check completed |

No independent performance percentages are claimed.

The one-case preparation smoke check verifies tooling output only.

## 14. Planned independent evaluation method

1. Freeze and record the code commit before evaluating.
2. Obtain 50 units not used to implement or tune the rules.
3. Record dataset provenance, unique units and financial-line counts.
4. Have two people label cases independently without seeing agent predictions.
5. Label evidence outcomes and potential-claim support separately.
6. Use `UNCERTAIN` where the evidence cannot establish a defensible answer.
7. Record the rationale and financial basis for each label.
8. Preserve disagreements and document any adjudication.
9. Compare frozen predictions with the human labels.
10. Report included, excluded and disagreement counts.

Report per charge type and outcome:

- True positives.
- False positives.
- False negatives.
- Precision and recall where denominators are nonzero.
- Human-label agreement.
- Abstention or uncertainty rate.
- Potential-claim amount correctness where independently established.

Undefined metrics must be reported as unavailable rather than as zero or perfect performance.

Any later rule changes require a clearly identified new evaluation run.

## 15. Failure modes and limitations

### Unverified policy applicability

The policy-source retrieval attempt remains pending source review. Marketplace, historical applicability and dispute eligibility are unverified.

A contradiction in source records does not prove a legitimate recoverable claim.

### Unverified source authenticity

Structured observations are accepted as supplied. The system does not authenticate photographs or prove that an operational event occurred.

### Incomplete financial history

No linked reimbursement does not establish that no reimbursement exists elsewhere.

### Limited charge coverage

Specific packaging and return-receipt reasoning are implemented. Weight-tier calculations, channel-loss proof, warehouse-damage responsibility and inventory valuation remain limited.

### Matching dependency

Missing or inconsistent identifiers can prevent relevant evidence retrieval or require uncertainty.

### Synthetic development provenance

Development cases are invented. Their outcomes cannot be used as real-world accuracy evidence.

### AI summary errors

Narrative summaries can misstate facts or terminology. They require review against original records and do not approve claims.

### Non-durable background processing

An API-process interruption can leave summary work pending.

### Decision versioning

Reassessment creates new versions. Earlier reviews remain attached to earlier decisions.

### Prototype authentication

Shared organisation tokens do not identify individual reviewers or provide production account controls.

### Deployment differences

Local checks do not establish equivalent behaviour in every deployed environment.

## 16. Current assessment

The implementation has demonstrated tested parsing, organisation isolation, conservative structured reasoning, reimbursement reconciliation, conditional amount calculation, review preservation and export workflows.

Independent evidence of claim accuracy remains unavailable.

Policy applicability, source authenticity, official evidence-contract compatibility and the required independent evaluation remain outstanding.