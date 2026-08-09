"""
webhook_client.py
------------------
Fires an outbound HTTP POST the instant a detection event is logged
(weapon / plate / anomaly / violence), instead of relying on the frontend
to poll GET /analysis/jobs/{id}/report and notice a new row.

Deliberately stdlib-only (urllib) so it doesn't add a new dependency, and
runs every send on a background daemon thread so a slow/unreachable
webhook endpoint can never stall frame processing or the analysis loop.

Configure via env:
    WEBHOOK_URLS      comma-separated list of URLs to POST to (empty = disabled)
    WEBHOOK_TIMEOUT   seconds, default 5
    WEBHOOK_SECRET    optional, sent as X-Netra-Signature header (HMAC-SHA256
                       of the raw JSON body) so receivers can verify the
                       payload actually came from this server
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List

logger = logging.getLogger("netra.webhook")

WEBHOOK_URLS: List[str] = [
    url.strip()
    for url in os.getenv("WEBHOOK_URLS", "").split(",")
    if url.strip()
]

WEBHOOK_TIMEOUT: float = float(os.getenv("WEBHOOK_TIMEOUT", "5"))
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")


def _sign(body: bytes) -> str:
    return hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _post(url: str, body: bytes, headers: Dict[str, str]) -> None:
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT) as response:
            logger.info(
                "Webhook delivered url=%s status=%s",
                url,
                response.status,
            )
    except urllib.error.HTTPError as exc:
        logger.warning(
            "Webhook endpoint rejected payload url=%s status=%s",
            url,
            exc.code,
        )
    except Exception:
        logger.exception(
            "Webhook delivery failed url=%s (endpoint unreachable or timed out)",
            url,
        )


def send_event_webhook(job_id: str, event: Dict[str, Any]) -> None:
    """
    Fire-and-forget: dispatch `event` to every configured webhook URL on
    its own background thread. Never raises -- a broken/slow webhook
    receiver must never be able to affect analysis itself.
    """
    if not WEBHOOK_URLS:
        return

    payload = {
        "job_id": job_id,
        "event": event,
    }

    body = json.dumps(payload, default=str).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if WEBHOOK_SECRET:
        headers["X-Netra-Signature"] = _sign(body)

    for url in WEBHOOK_URLS:
        threading.Thread(
            target=_post,
            args=(url, body, headers),
            daemon=True,
        ).start()