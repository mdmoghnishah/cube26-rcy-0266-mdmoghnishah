import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SOURCE_URL = (
    "https://sellercentral.amazon.com/help/hub/"
    "reference/external/G201685210"
)

SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "policy_snapshots"
    / "amazon_us_bagging.json"
)


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def base_record():
    return {
        "policy_id": "amazon-us-bagging",
        "channel": "amazon",
        "marketplace": "US",
        "source_url": SOURCE_URL,
        "retrieval_status": "NOT_RETRIEVED",
        "retrieved_at": None,
        "content_sha256": None,
        "retrieved_text": None,
        "candidate_sealing_passages": [],
        "effective_from": None,
        "applicability_status": "UNVERIFIED",
        "claim_eligibility_status": "UNVERIFIED",
        "error": None,
    }


def retrieve_policy():
    record = base_record()

    request = Request(
        SOURCE_URL,
        headers={"User-Agent": "RecoveryManager/0.1"},
    )

    try:
        with urlopen(request, timeout=15) as response:
            final_url = urlsplit(response.geturl())

            if (
                final_url.scheme != "https"
                or final_url.hostname != "sellercentral.amazon.com"
            ):
                raise ValueError("Unexpected source destination.")

            if (
                "/ap/signin" in final_url.path
                or "/ap/" in final_url.path
            ):
                raise ValueError("Source requires authentication.")

            content_type = response.headers.get(
                "Content-Type", ""
            )

            if "text/html" not in content_type:
                raise ValueError("Expected an HTML policy page.")

            body = response.read(2_000_001)

            if len(body) > 2_000_000:
                raise ValueError("Policy response exceeds size limit.")

            encoding = (
                response.headers.get_content_charset()
                or "utf-8"
            )

        parser = PageText()
        parser.feed(body.decode(encoding, errors="replace"))
        passages = [
            re.sub(r"\s+", " ", part).strip()
            for part in parser.parts
        ]

        candidates = [
            passage for passage in passages
            if (
                re.search(r"poly\s*bag", passage, re.I)
                and re.search(r"\bseal\w*\b", passage, re.I)
            )
        ]

        if not candidates:
            raise ValueError(
                "Readable bag-sealing content was not found. "
                "The source may require JavaScript or authentication."
            )

        record.update(
            retrieval_status="RETRIEVED_NEEDS_REVIEW",
            retrieved_at=datetime.now(
                timezone.utc
            ).isoformat(),
            content_sha256=hashlib.sha256(body).hexdigest(),
            retrieved_text="\n".join(passages),
            candidate_sealing_passages=candidates,
        )

    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        record.update(
            retrieval_status="PENDING_SOURCE_REVIEW",
            error=(
                "Policy content could not be reliably retrieved. "
                "Obtain the applicable official document through "
                "an authorised channel and review it."
            ),
        )

    # Do not erase a previous retrieved snapshot after a failed attempt.
    # Record each attempt in a separate file.
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

    attempt_id = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    attempt_path = SNAPSHOT_PATH.with_name(
        f"amazon_us_bagging_attempt_{attempt_id}.json"
    )
    attempt_path.write_text(
        json.dumps(record, indent=2),
        encoding="utf-8",
    )

    if (
        record["retrieval_status"] == "RETRIEVED_NEEDS_REVIEW"
        or not SNAPSHOT_PATH.exists()
    ):
        SNAPSHOT_PATH.write_text(
            json.dumps(record, indent=2),
            encoding="utf-8",
        )

    return record


def policy_context(charge):
    if (
        charge.get("charge_type") != "inbound_defect_fee"
        or charge.get("alleged_defect") != "polybag_not_sealed"
    ):
        return {
            "status": "NO_POLICY_ADAPTER",
            "applicability_status": "UNVERIFIED",
            "reason": "No policy source is configured for this allegation.",
        }

    try:
        snapshot = json.loads(
            SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        snapshot = base_record()

    return {
        "policy_id": snapshot["policy_id"],
        "source_url": snapshot["source_url"],
        "source_marketplace": "US",
        "charge_marketplace": charge.get("marketplace"),
        "retrieval_status": snapshot["retrieval_status"],
        "retrieved_at": snapshot.get("retrieved_at"),
        "content_sha256": snapshot.get("content_sha256"),
        "candidate_sealing_passages": snapshot.get(
            "candidate_sealing_passages", []
        ),
        "applicability_status": "UNVERIFIED",
        "claim_eligibility_status": "UNVERIFIED",
        "reason": (
            "Source retrieval does not verify marketplace, "
            "product applicability, event-date policy or "
            "dispute eligibility. Human review is required."
        ),
    }


if __name__ == "__main__":
    result = retrieve_policy()
    print("Policy retrieval:", result["retrieval_status"])
    print("Applicability:", result["applicability_status"])
    print("Snapshot:", SNAPSHOT_PATH)

    if result["error"]:
        print(result["error"])