from __future__ import annotations
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("netra.webhook")

WEBHOOK_URLS: List[str] = [
    url.strip()
    for url in os.getenv("WEBHOOK_URLS", "").split(",")
    if url.strip()
]

WEBHOOK_TIMEOUT: float = float(os.getenv("WEBHOOK_TIMEOUT", "4"))
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_RETRIES: int = int(os.getenv("WEBHOOK_RETRIES", "2"))

def configured_urls() -> List[str]:
    return list(WEBHOOK_URLS)

def _sign(body: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

def _post(url: str, body: bytes, headers: Dict[str, str]) -> None:
    last_err: Optional[BaseException] = None
    for attempt in range(1, max(1, WEBHOOK_RETRIES) + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT) as response:
                logger.info(
                    "Webhook delivered url=%s status=%s attempt=%s",
                    url,
                    response.status,
                    attempt,
                )
                return
        except urllib.error.HTTPError as exc:
            last_err = exc
            logger.warning(
                "Webhook rejected url=%s status=%s attempt=%s",
                url,
                exc.code,
                attempt,
            )
            if exc.code < 500:
                return
        except Exception as exc:
            last_err = exc
            logger.warning(
                "Webhook delivery failed url=%s attempt=%s err=%s",
                url,
                attempt,
                exc,
            )
        time.sleep(0.15 * attempt)
    if last_err is not None:
        logger.error("Webhook gave up url=%s after %s attempts", url, WEBHOOK_RETRIES)

def send_alert_webhook(
    payload: Dict[str, Any],
    *,
    urls: Optional[List[str]] = None,
    secret: Optional[str] = None,
) -> None:
    targets = [u.strip() for u in (urls if urls is not None else WEBHOOK_URLS) if u and u.strip()]
    if not targets:
        return

    body = json.dumps(payload, default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "NETRA-Alerting/1.0",
        "X-Netra-Alert-Id": str(payload.get("alert_id") or ""),
        "X-Netra-Event-Type": str((payload.get("event") or {}).get("type") or ""),
    }
    use_secret = secret if secret is not None else WEBHOOK_SECRET
    if use_secret:
        headers["X-Netra-Signature"] = _sign(body, use_secret)

    for url in targets:
        threading.Thread(
            target=_post,
            args=(url, body, headers),
            daemon=True,
            name="netra-webhook-post",
        ).start()


def send_event_webhook(job_id: str, event: Dict[str, Any]) -> None:
    if not WEBHOOK_URLS:
        return

    payload = {
        "alert_id": event.get("event_id") or job_id,
        "kind": "netra.event",
        "fired_at": event.get("wall_clock_time"),
        "job_id": job_id,
        "event": event,
        "context": {
            "snapshot_url": event.get("snapshot_url"),
            "bbox": event.get("bbox"),
            "location": event.get("location"),
        },
    }
    send_alert_webhook(payload, urls=None, secret=None)