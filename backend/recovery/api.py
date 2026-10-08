import os
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks
from recovery.ai import summarize_unit

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from recovery.db import connection
from recovery.decisions import assess_organization
from recovery.packets import build_review_packet

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from recovery.report_upload import (
    MAX_BYTES,
    ReportValidationError,
    import_report,
)
from recovery import round3

app = FastAPI(
    title="Recovery Manager",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

security = HTTPBearer()


def authenticated_org(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    tokens = [
        (os.getenv("ALPHA_TOKEN", ""), "org_demo_alpha"),
        (os.getenv("BRAVO_TOKEN", ""), "org_demo_bravo"),
    ]

    if (
        any(len(token) < 32 for token, _ in tokens)
        or tokens[0][0] == tokens[1][0]
    ):
        raise HTTPException(
            status_code=503,
            detail="Configure two distinct organization tokens.",
        )

    for token, organization in tokens:
        if secrets.compare_digest(
            credentials.credentials,
            token,
        ):
            return organization

    raise HTTPException(
        status_code=401,
        detail="Invalid access token.",
    )


class ReviewInput(BaseModel):
    assessment: Literal[
        "CONTRADICTS",
        "SUPPORTS",
        "SILENT",
        "UNCERTAIN",
    ]
    reason: str = Field(min_length=10, max_length=2000)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Recovery Manager",
        "stage": round3.STAGE,
        "agent_id": round3.AGENT_ID,
        "contract_version": round3.CONTRACT_VERSION,
        "round3": {
            "run": "/run",
            "demo_cases": "/demo/cases",
            "rule_version": round3.RULE_VERSION,
            "anthropic_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
        },
    }


@app.post("/run")
async def run_round3(request: Request):
    """Round 3 orchestrator entry: Agent Input in, Agent Output out.

    No bearer token: the orchestrator is trusted and the subject org is
    restricted to the demo organisations. 422 malformed, 404 unknown
    tenant, otherwise 200 with a completed or fail-open pending record.
    """
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_agent_input", "detail": "Body must be JSON."},
        )

    status_code, output = await run_in_threadpool(round3.run_round3, body)
    return JSONResponse(status_code=status_code, content=output)


@app.get("/demo/cases")
def demo_cases():
    """Frozen stories for the stage. They do not call a model."""
    cases = []
    for case in round3.demo_cases():
        _status, output = round3.run_round3(case["body"])
        cases.append(
            {
                "case_id": case["case_id"],
                "title": case["title"],
                "expect_outcome": case["expect_outcome"],
                "outcome": output["evidence"]["decision"]["outcome"],
                "verdict": output["verdict"],
                "claimable_usd": output["evidence"]["payload"]["claimable_usd"],
                "claim_filed": False,
            }
        )
    return {"cases": cases}


@app.get("/me")
def me(org_id=Depends(authenticated_org)):
    return {"org_id": org_id}


@app.get("/decisions")
def list_decisions(org_id=Depends(authenticated_org)):
    with connection(org_id) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (result->>'line_id')
                id, result, created_at
            FROM recovery.decisions
            WHERE result ? 'line_id'
            ORDER BY
                result->>'line_id',
                created_at DESC,
                id DESC
            """
        ).fetchall()

    return {
        "org_id": org_id,
        "decisions": rows,
    }


@app.get("/decisions/{decision_id}")
def get_decision(
    decision_id: UUID,
    org_id=Depends(authenticated_org),
):
    with connection(org_id) as conn:
        row = conn.execute(
            """
            SELECT id, result, created_at
            FROM recovery.decisions
            WHERE id = %s
            """,
            (decision_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Decision not found.",
            )

        reviews = conn.execute(
            """
            SELECT id, review, created_at
            FROM recovery.reviews
            WHERE decision_id = %s
            ORDER BY created_at, id
            """,
            (decision_id,),
        ).fetchall()

    return {
        **row,
        "reviews": reviews,
    }


@app.post("/assessments")
def run_assessment(org_id=Depends(authenticated_org)):
    results = assess_organization(org_id)

    return {
        "org_id": org_id,
        "saved": len(results),
    }


@app.post("/decisions/{decision_id}/reviews")
def save_review(
    decision_id: UUID,
    body: ReviewInput,
    org_id=Depends(authenticated_org),
):
    reason = body.reason.strip()

    if len(reason) < 10:
        raise HTTPException(
            status_code=422,
            detail="Provide a reason of at least 10 characters.",
        )

    with connection(org_id) as conn:
        decision = conn.execute(
            """
            SELECT result
            FROM recovery.decisions
            WHERE id = %s
            """,
            (decision_id,),
        ).fetchone()

        if decision is None:
            raise HTTPException(
                status_code=404,
                detail="Decision not found.",
            )

        review_id = uuid4()

        review = {
            "assessment": body.assessment,
            "reason": reason,
            "reviewer": f"{org_id}:demo-token-holder",
            "claim_status": "REVIEW",
        }

        conn.execute(
            """
            INSERT INTO recovery.reviews (
                id,
                org_id,
                decision_id,
                original,
                review
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                review_id,
                org_id,
                decision_id,
                Jsonb(decision["result"]),
                Jsonb(review),
            ),
        )

    return {
        "id": review_id,
        "message": "Review saved. Original decision retained.",
    }

@app.post("/decisions/{decision_id}/ai-summary")
def generate_ai_summary(
    decision_id: UUID,
    background_tasks: BackgroundTasks,
    org_id=Depends(authenticated_org),
):
    with connection(org_id) as conn:
        decision = conn.execute(
            """
            SELECT id
            FROM recovery.decisions
            WHERE id = %s
              AND result ? 'unit_id'
            """,
            (decision_id,),
        ).fetchone()

        if decision is None:
            raise HTTPException(
                status_code=404,
                detail="Decision not found.",
            )

        conn.execute(
            """
            UPDATE recovery.decisions
            SET result = result || %s
            WHERE id = %s
            """,
            (
                Jsonb({
                    "ai_status": "pending",
                    "ai_summary": None,
                    "ai_error": None,
                }),
                decision_id,
            ),
        )

    background_tasks.add_task(
        summarize_unit,
        org_id,
        decision_id,
    )

    return {
        "message": "AI summary queued. Refresh the decision shortly."
    }
@app.get("/decisions/{decision_id}/review-packet")
def get_review_packet(
    decision_id: UUID,
    org_id=Depends(authenticated_org),
):
    decision = get_decision(
        decision_id=decision_id,
        org_id=org_id,
    )

    return build_review_packet(decision)
@app.post("/reports/upload", status_code=201)
async def upload_report(
    request: Request,
    org_id=Depends(authenticated_org),
):
    content_type = (
        request.headers.get("content-type", "")
        .split(";", 1)[0]
        .strip()
        .lower()
    )

    if content_type not in {
        "text/csv",
        "application/csv",
        "application/octet-stream",
    }:
        raise HTTPException(
            status_code=415,
            detail="Send the CSV file as the request body.",
        )

    content = bytearray()

    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail="CSV file must be at most 2 MB.",
            )

        content.extend(chunk)

    try:
        imported = await run_in_threadpool(
            import_report,
            bytes(content),
            org_id,
        )

    except ReportValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Report validation failed. No rows imported.",
                "errors": error.errors,
            },
        ) from None

    return {
        **imported,
        "assessment_status": "NOT_STARTED",
        "message": (
            "Report imported. Run assessments to refresh decisions."
        ),
    }