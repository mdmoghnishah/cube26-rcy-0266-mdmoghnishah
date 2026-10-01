"use client";

import { useRef, useState } from "react";

const API =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const VERDICTS = [
  "UNCERTAIN",
  "SILENT",
  "SUPPORTS",
  "CONTRADICTS",
];

export default function Home() {
  const [token, setToken] = useState("");
  const [organization, setOrganization] = useState("");
  const [decisions, setDecisions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [reviewVerdict, setReviewVerdict] = useState("UNCERTAIN");
  const [reviewReason, setReviewReason] = useState("");

  // Ignore responses from an earlier connection or selection.
  const requestVersion = useRef(0);

  async function request(
    path,
    accessToken,
    method = "GET",
    payload
  ) {
    const headers = {
      Authorization: `Bearer ${accessToken}`,
    };

    if (payload !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    const response = await fetch(`${API}${path}`, {
      method,
      headers,
      body:
        payload !== undefined
          ? JSON.stringify(payload)
          : undefined,
    });

    const body = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = body?.detail;

      throw new Error(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => item.msg).join("; ")
            : `Request failed (${response.status}).`
      );
    }

    return body;
  }

  async function connect() {
    const version = ++requestVersion.current;
    const accessToken = token.trim();

    setBusy(true);
    setMessage("");
    setOrganization("");
    setDecisions([]);
    setSelected(null);
    setReviewReason("");

    try {
      const identity = await request("/me", accessToken);
      const data = await request("/decisions", accessToken);

      if (version !== requestVersion.current) return;

      setOrganization(identity.org_id);
      setDecisions(data.decisions);
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }

  async function reassess() {
    const version = ++requestVersion.current;
    const accessToken = token.trim();

    setBusy(true);
    setMessage("");

    try {
      await request("/assessments", accessToken, "POST");
      const data = await request("/decisions", accessToken);

      if (version !== requestVersion.current) return;

      setDecisions(data.decisions);
      setSelected(null);
      setReviewReason("");
      setMessage(
        "New assessments saved. Earlier decisions and reviews retained."
      );
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }

  async function selectDecision(item) {
    const version = ++requestVersion.current;

    setBusy(true);
    setMessage("");
    setSelected(null);
    setReviewReason("");

    try {
      const detail = await request(
        `/decisions/${item.id}`,
        token.trim()
      );

      if (version !== requestVersion.current) return;

      setSelected(detail);

      const latestReview = detail.reviews?.at(-1);

      setReviewVerdict(
        latestReview?.review.assessment ||
        detail.result.assessment
      );
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }

  async function saveReview() {
    if (!selected || reviewReason.trim().length < 10) return;

    const version = ++requestVersion.current;
    const decisionId = selected.id;
    const accessToken = token.trim();

    setBusy(true);
    setMessage("");

    try {
      const result = await request(
        `/decisions/${decisionId}/reviews`,
        accessToken,
        "POST",
        {
          assessment: reviewVerdict,
          reason: reviewReason.trim(),
        }
      );

      if (version !== requestVersion.current) return;

      // The save succeeded even if the subsequent refresh fails.
      setMessage(result.message);
      setReviewReason("");

      try {
        const detail = await request(
          `/decisions/${decisionId}`,
          accessToken
        );

        if (version === requestVersion.current) {
          setSelected(detail);
        }
      } catch {
        if (version === requestVersion.current) {
          setMessage(
            "Review saved, but history could not refresh. Select the report line again to reload it."
          );
        }
      }
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }

  function changeToken(value) {
    requestVersion.current += 1;

    setToken(value);
    setOrganization("");
    setDecisions([]);
    setSelected(null);
    setReviewReason("");
    setReviewVerdict("UNCERTAIN");
    setBusy(false);
    setMessage("");
  }

  function downloadDecision() {
    if (!selected) return;

    const blob = new Blob(
      [JSON.stringify(selected, null, 2)],
      { type: "application/json" }
    );

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download =
      `${selected.result.line_id}-assessment.json`;

    document.body.appendChild(link);
    link.click();
    link.remove();

    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const uncertain = decisions.filter(
    (item) => item.result.assessment === "UNCERTAIN"
  ).length;

  const silent = decisions.filter(
    (item) => item.result.assessment === "SILENT"
  ).length;

  async function generateAiSummary() {
    if (!selected) return;

    const version = ++requestVersion.current;
    const decisionId = selected.id;

    setBusy(true);
    setMessage("");

    try {
      const result = await request(
        `/decisions/${decisionId}/ai-summary`,
        token.trim(),
        "POST"
      );

      if (version === requestVersion.current) {
        setMessage(
          `${result.message} Select the report line again after a few seconds to refresh it.`
        );
      }
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }
  async function downloadReviewPacket() {
    if (!selected) return;

    const version = ++requestVersion.current;
    const decisionId = selected.id;

    setBusy(true);
    setMessage("");

    try {
      const packet = await request(
        `/decisions/${decisionId}/review-packet`,
        token.trim()
      );

      if (version !== requestVersion.current) return;

      const blob = new Blob(
        [JSON.stringify(packet, null, 2)],
        { type: "application/json" }
      );

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = url;
      link.download = `${packet.line_id}-review-packet.json`;

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(url), 1000);

      setMessage(
        "Review packet downloaded. Eligibility and claim amount still require verification."
      );
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }
  async function showPotentialClaim() {
    if (!selected) return;

    const version = ++requestVersion.current;
    setBusy(true);
    setMessage("");

    try {
      const packet = await request(
        `/decisions/${selected.id}/review-packet`,
        token.trim()
      );

      if (version !== requestVersion.current) return;

      setSelected((current) => ({
        ...current,
        potentialClaim: packet.potential_claim,
      }));
    } catch (error) {
      if (version === requestVersion.current) {
        setMessage(error.message);
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
      }
    }
  }

  return (
    <main>
      <header>
        <strong>CUBE / RECOVERY MANAGER</strong>
        <span>Evidence before claims</span>
      </header>

      <section className="intro">
        <p className="eyebrow">SELLER OPERATIONS</p>

        <h1>Every charge deserves a clear explanation.</h1>

        <p>
          Match financial reports with operational records.
          Review the evidence, missing information, and decision
          behind each charge.
        </p>
      </section>

      <section className="panel access">
        <label htmlFor="token">
          Organization access token
        </label>

        <div className="access-row">
          <input
            id="token"
            type="password"
            placeholder="Enter Alpha or Bravo token"
            value={token}
            onChange={(event) =>
              changeToken(event.target.value)
            }
            autoComplete="off"
          />

          <button
            onClick={connect}
            disabled={busy || !token.trim()}
          >
            {busy ? "Working…" : "Connect"}
          </button>
        </div>

        {organization && (
          <p className="organization">
            Connected: {organization}
          </p>
        )}
      </section>

      {message && (
        <p className="message" role="status">
          {message}
        </p>
      )}

      {organization && (
        <>
          <section className="metrics">
            <div className="panel">
              <span>Financial lines</span>
              <strong>{decisions.length}</strong>
            </div>

            <div className="panel">
              <span>Agent: uncertain / needs review</span>
              <strong>{uncertain}</strong>
            </div>

            <div className="panel">
              <span>
                Agent: silent / reimbursement entries
              </span>
              <strong>{silent}</strong>
            </div>
          </section>

          <div className="toolbar">
            <h2>Charge assessments</h2>

            <button
              onClick={reassess}
              disabled={busy}
            >
              Reassess records
            </button>
          </div>

          <section className="workspace">
            <div className="panel table-panel">
              <table>
                <thead>
                  <tr>
                    <th>Report line</th>
                    <th>Amount</th>
                    <th>Agent assessment</th>
                  </tr>
                </thead>

                <tbody>
                  {decisions.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() =>
                            selectDecision(item)
                          }
                        >
                          {item.result.line_id}
                        </button>

                        <small>
                          {item.result.charge_type.replaceAll(
                            "_",
                            " "
                          )}
                        </small>
                      </td>

                      <td>
                        ${item.result.charge.amount_usd}
                      </td>

                      <td>
                        <span
                          className={`badge ${item.result.assessment.toLowerCase()}`}
                        >
                          {item.result.assessment}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {!decisions.length && (
                <p className="empty">
                  No saved assessments. Import records and
                  run an assessment first.
                </p>
              )}
            </div>

            <aside className="panel">
              {selected ? (
                <>
                  <p className="eyebrow">
                    DECISION DETAILS
                  </p>

                  <h2>{selected.result.line_id}</h2>

                  <p>{selected.result.reason}</p>

                  <dl>
                    <dt>Unit</dt>
                    <dd>{selected.result.unit_id}</dd>

                    <dt>Original agent assessment</dt>
                    <dd>
                      {selected.result.assessment}
                    </dd>

                    <dt>Claim status</dt>
                    <dd>
                      {selected.result.claim_status}
                    </dd>

                    <dt>Supported claim amount</dt>
                    <dd>
                      {selected.result.claim_amount_usd == null
                        ? "Not established"
                        : `$${selected.result.claim_amount_usd}`}
                    </dd>
                  </dl>
                  {selected.result.reconciliation && (
                    <>
                      <h3>Reimbursement reconciliation</h3>

                      <dl>
                        <dt>Status</dt>
                        <dd>
                          {selected.result.reconciliation.status}
                        </dd>

                        <dt>Recorded linked reimbursements</dt>
                        <dd>
                          {selected.result.reconciliation
                            .recorded_reimbursement_usd == null
                            ? "Not established"
                            : `$${selected.result.reconciliation.recorded_reimbursement_usd}`}
                        </dd>

                        <dt>Remaining reported balance</dt>
                        <dd>
                          {selected.result.reconciliation
                            .unreimbursed_reported_amount_usd == null
                            ? "Not established"
                            : `$${selected.result.reconciliation.unreimbursed_reported_amount_usd}`}
                        </dd>
                      </dl>

                      {selected.result.reconciliation.reason && (
                        <p>{selected.result.reconciliation.reason}</p>
                      )}

                      {selected.result.reconciliation.flags.length > 0 && (
                        <p>
                          Flags:{" "}
                          {selected.result.reconciliation.flags.join(", ")}
                        </p>
                      )}

                      <p className="note">
                        Reconciliation uses imported report entries.
                        A remaining balance is not an approved claim amount.
                      </p>
                    </>
                  )}
                  <h3>Potential recovery claim</h3>

                  <button onClick={showPotentialClaim} disabled={busy}>
                    Calculate potential claim
                  </button>

                  {selected.potentialClaim && (
                    <>
                      <dl>
                        <dt>Status</dt>
                        <dd>{selected.potentialClaim.status}</dd>

                        <dt>Potential amount</dt>
                        <dd>
                          {selected.potentialClaim.potential_amount_usd == null
                            ? "Not established"
                            : `$${selected.potentialClaim.potential_amount_usd}`}
                        </dd>
                      </dl>

                      <p>{selected.potentialClaim.reason}</p>

                      {selected.potentialClaim.amount_basis && (
                        <p>{selected.potentialClaim.amount_basis}</p>
                      )}

                      <p className="note">
                        Conditional estimate. Not approved or ready to submit.
                        Current demonstration records are synthetic.
                      </p>

                      <details>
                        <summary>Required verifications</summary>

                        <ul style={{ paddingLeft: "24px", marginTop: "12px" }}>
                          {(selected.potentialClaim?.required_verifications ?? []).map(
                            (check, index) => (
                              <li
                                key={`${index}-${check}`}
                                style={{ marginBottom: "8px", color: "#24332b" }}
                              >
                                {check}
                              </li>
                            )
                          )}
                        </ul>
                      </details>
                    </>
                  )}

                  <h3>Matched evidence</h3>

                  {selected.result.evidence.length ? (
                    selected.result.evidence.map(
                      (record) => (
                        <details
                          key={`${record.manager}-${record.record_id}`}
                        >
                          <summary>
                            {record.record_id} ·{" "}
                            {record.manager}
                          </summary>

                          <p>
                            Captured:{" "}
                            {record.captured_at}
                          </p>

                          {record.identifier_conflicts
                            .length > 0 && (
                              <p>
                                Identifier conflicts:{" "}
                                {record.identifier_conflicts.join(
                                  ", "
                                )}
                              </p>
                            )}

                          <p className="note">
                            Photo paths are sample
                            references. No verified image
                            is attached.
                          </p>

                          <pre>
                            {JSON.stringify(
                              record.raw,
                              null,
                              2
                            )}
                          </pre>
                        </details>
                      )
                    )
                  ) : (
                    <p>
                      No relevant evidence matched.
                    </p>
                  )}


                  <h3>AI evidence summary</h3>

                  <p className="note">
                    AI-generated reviewer aid. Verify it against the original records.
                  </p>

                  <p>
                    Status: {selected.result.ai_status || "Not requested"}
                  </p>

                  {selected.result.ai_summary && (
                    <p style={{ whiteSpace: "pre-wrap" }}>
                      {selected.result.ai_summary}
                    </p>
                  )}

                  {selected.result.ai_error && (
                    <p>{selected.result.ai_error}</p>
                  )}

                  <button
                    onClick={generateAiSummary}
                    disabled={busy}
                  >
                    Generate unit summary
                  </button>

                  <h3>Human review</h3>

                  <label htmlFor="review-verdict">
                    Reviewed assessment
                  </label>

                  <select
                    id="review-verdict"
                    value={reviewVerdict}
                    disabled={busy}
                    onChange={(event) =>
                      setReviewVerdict(
                        event.target.value
                      )
                    }
                  >
                    {VERDICTS.map((value) => (
                      <option
                        key={value}
                        value={value}
                      >
                        {value}
                      </option>
                    ))}
                  </select>

                  <label htmlFor="review-reason">
                    Reason for your review
                  </label>

                  <textarea
                    id="review-reason"
                    value={reviewReason}
                    disabled={busy}
                    onChange={(event) =>
                      setReviewReason(
                        event.target.value
                      )
                    }
                    placeholder="Explain your decision using the available evidence."
                    maxLength={2000}
                  />

                  <button
                    onClick={saveReview}
                    disabled={
                      busy ||
                      reviewReason.trim().length < 10
                    }
                  >
                    Save human review
                  </button>

                  <p className="note">
                    The original agent decision remains
                    unchanged. A human verdict does not
                    approve a claim amount.
                  </p>

                  <h3>Saved review history</h3>

                  {selected.reviews?.length ? (
                    selected.reviews.map((entry) => (
                      <details key={entry.id}>
                        <summary>
                          {entry.review.assessment} ·{" "}
                          {new Date(
                            entry.created_at
                          ).toLocaleString()}
                        </summary>

                        <p>{entry.review.reason}</p>

                        <p className="note">
                          Reviewer:{" "}
                          {entry.review.reviewer}
                        </p>
                      </details>
                    ))
                  ) : (
                    <p>No human reviews recorded.</p>
                  )}

                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "12px",
                      marginTop: "16px",
                    }}
                  >
                    <button onClick={downloadDecision} disabled={busy}>
                      Download assessment + reviews JSON
                    </button>

                    <button onClick={downloadReviewPacket} disabled={busy}>
                      Download recovery review packet
                    </button>
                  </div>
                </>
              ) : (
                <p className="empty">
                  Select a report line to inspect its
                  evidence, explanation, and review history.
                </p>
              )}
            </aside>
          </section>
        </>
      )}

      <footer>
        Synthetic sample records · No claims filed
        automatically · AI summaries require verification
      </footer>
    </main>
  );
}