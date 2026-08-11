"""
alerting.py
-----------
Low-latency alert pipeline for N.E.T.R.A.

Design goals (Sub-5s alerting across simultaneous streams):
  - Evaluation is synchronous and cheap (rule match + cooldown).
  - Delivery runs on a shared ThreadPoolExecutor so many live/upload
    streams can fire alerts at once without blocking frame processing.
  - Payload always includes snapshot context; optional short clip is
    cut in the worker (best-effort, skipped if it would exceed budget).
  - Routing uses configurable webhook destinations + rules/watchlists
    from alert_config.py.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import cv2

import alert_config
import webhook_client
from config import Config

logger = logging.getLogger("netra.alerting")

ALERT_WORKERS = int(os.getenv("ALERT_WORKERS", "6"))
CLIP_SECONDS_BEFORE = float(os.getenv("ALERT_CLIP_SECONDS_BEFORE", "2.0"))
CLIP_SECONDS_AFTER = float(os.getenv("ALERT_CLIP_SECONDS_AFTER", "2.0"))
CLIP_TIME_BUDGET_S = float(os.getenv("ALERT_CLIP_TIME_BUDGET_S", "3.5"))

_executor = ThreadPoolExecutor(max_workers=ALERT_WORKERS, thread_name_prefix="netra-alert")
_lock = threading.RLock()
_cooldowns: Dict[str, float] = {}  # key -> monotonic timestamp of last fire
_recent_alerts: List[Dict[str, Any]] = []
_MAX_RECENT = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _absolute_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = alert_config.public_base_url()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _cooldown_key(job_id: str, rule_id: str, event_type: str, label: str) -> str:
    return f"{job_id}|{rule_id}|{event_type}|{label}"


def _under_cooldown(key: str, cooldown_seconds: float) -> bool:
    now = time.monotonic()
    with _lock:
        last = _cooldowns.get(key, 0.0)
        if now - last < cooldown_seconds:
            return True
        _cooldowns[key] = now
        return False


def _remember(alert: Dict[str, Any]) -> None:
    with _lock:
        _recent_alerts.insert(0, alert)
        del _recent_alerts[_MAX_RECENT:]


def list_recent_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    with _lock:
        return list(_recent_alerts[: max(1, min(limit, _MAX_RECENT))])


def _extract_clip(
    local_path: str,
    job_id: str,
    event_id: str,
    video_time_seconds: float,
) -> Optional[str]:
    """
    Cut a short evidence clip around the event. Returns a web URL or None.
    Soft time budget so clip work cannot push the whole alert past ~5s.
    """
    if not local_path or not os.path.isfile(local_path):
        return None

    started = time.perf_counter()
    cap = cv2.VideoCapture(local_path)
    if not cap.isOpened():
        return None

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 20.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            return None

        start_t = max(0.0, float(video_time_seconds) - CLIP_SECONDS_BEFORE)
        end_t = float(video_time_seconds) + CLIP_SECONDS_AFTER
        start_frame = int(start_t * fps)
        end_frame = int(end_t * fps)

        out_dir = os.path.join(Config.SNAPSHOT_DIR, job_id, "clips")
        os.makedirs(out_dir, exist_ok=True)
        filename = f"{event_id}.mp4"
        out_path = os.path.join(out_dir, filename)

        writer = cv2.VideoWriter(
            out_path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            return None

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_i = start_frame
        while frame_i <= end_frame:
            if (time.perf_counter() - started) > CLIP_TIME_BUDGET_S:
                break
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            writer.write(frame)
            frame_i += 1

        writer.release()
        if not os.path.isfile(out_path) or os.path.getsize(out_path) < 64:
            return None
        return f"/snapshots/{job_id}/clips/{filename}"
    except Exception:
        logger.exception("Clip extract failed job_id=%s event_id=%s", job_id, event_id)
        return None
    finally:
        try:
            cap.release()
        except Exception:
            pass


def _matching_rules(event: Dict[str, Any], job: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    event_type = (event.get("type") or "").lower()
    confidence = float(event.get("confidence") or 0.0)
    source_label = ""
    job_id = ""
    if job:
        source_label = str(job.get("original_name") or job.get("source") or "")
        job_id = str(job.get("job_id") or "")

    matches: List[Dict[str, Any]] = []
    for rule in alert_config.list_rules():
        if not rule.get("enabled", True):
            continue
        types = [t.lower() for t in (rule.get("event_types") or [])]
        if types and event_type not in types:
            continue
        min_conf = float(rule.get("min_confidence") or 0.0)
        if confidence < min_conf and event_type not in {"anomaly", "plate"}:
            # anomaly often has confidence 0; plates use detector conf separately
            if event_type != "anomaly":
                continue
        if event_type == "plate" and confidence < min_conf:
            continue

        labels = rule.get("stream_labels") or []
        if labels and source_label and source_label not in labels:
            continue
        stream_ids = rule.get("stream_ids") or []
        if stream_ids and job_id and job_id not in stream_ids:
            continue

        watchlist_id = rule.get("watchlist_id")
        watch_hit = None
        if watchlist_id:
            watch_hit = alert_config.match_watchlist(event, watchlist_id)
            if watch_hit is None:
                continue
        elif event_type == "plate" and rule.get("require_watchlist"):
            continue

        matches.append({"rule": rule, "watchlist_match": watch_hit})
    return matches


def _build_alert_payload(
    *,
    job_id: str,
    job: Optional[Dict[str, Any]],
    event: Dict[str, Any],
    rule: Dict[str, Any],
    watchlist_match: Optional[Dict[str, Any]],
    clip_url: Optional[str],
    latency_ms: float,
) -> Dict[str, Any]:
    snapshot = event.get("snapshot_url")
    return {
        "alert_id": uuid.uuid4().hex[:16],
        "kind": "netra.alert",
        "severity": rule.get("severity") or "high",
        "latency_ms": round(latency_ms, 1),
        "fired_at": _utc_now(),
        "rule": {
            "id": rule.get("id"),
            "name": rule.get("name"),
            "event_types": rule.get("event_types"),
        },
        "watchlist_match": watchlist_match,
        "stream": {
            "job_id": job_id,
            "source": (job or {}).get("source"),
            "original_name": (job or {}).get("original_name"),
            "stream_url": (job or {}).get("stream_url"),
            "status": (job or {}).get("status"),
        },
        "event": {
            "event_id": event.get("event_id"),
            "type": event.get("type"),
            "label": event.get("label"),
            "plate_number": event.get("plate_number"),
            "confidence": event.get("confidence"),
            "frame_number": event.get("frame_number"),
            "video_time_seconds": event.get("video_time_seconds"),
            "video_timestamp": event.get("video_timestamp"),
            "location": event.get("location"),
            "bbox": event.get("bbox"),
        },
        "context": {
            "snapshot_url": _absolute_url(snapshot),
            "snapshot_path": snapshot,
            "clip_url": _absolute_url(clip_url),
            "clip_path": clip_url,
            "annotated_video_url": _absolute_url(
                (job or {}).get("annotated_video_url")
                or ((job or {}).get("result") or {}).get("annotated_video_url")
            ),
        },
    }


def _deliver_alert(
    *,
    job_id: str,
    job: Optional[Dict[str, Any]],
    event: Dict[str, Any],
    rule: Dict[str, Any],
    watchlist_match: Optional[Dict[str, Any]],
    queued_at: float,
) -> None:
    t0 = queued_at
    clip_url = None
    if rule.get("include_clip", True):
        local_path = (job or {}).get("local_path")
        event_id = event.get("event_id") or uuid.uuid4().hex[:12]
        video_t = float(event.get("video_time_seconds") or 0.0)
        if local_path:
            clip_url = _extract_clip(local_path, job_id, event_id, video_t)

    latency_ms = (time.perf_counter() - t0) * 1000.0
    payload = _build_alert_payload(
        job_id=job_id,
        job=job,
        event=event,
        rule=rule,
        watchlist_match=watchlist_match,
        clip_url=clip_url,
        latency_ms=latency_ms,
    )

    # Route to matching webhooks (type + confidence filters per destination)
    event_type = (event.get("type") or "").lower()
    confidence = float(event.get("confidence") or 0.0)
    destinations = []
    for wh in alert_config.list_webhooks():
        if not wh.get("enabled", True):
            continue
        url = (wh.get("url") or "").strip()
        if not url:
            continue
        types = [t.lower() for t in (wh.get("event_types") or [])]
        if types and event_type not in types:
            continue
        if confidence < float(wh.get("min_confidence") or 0.0) and event_type != "anomaly":
            continue
        destinations.append(wh)

    # Fallback: env WEBHOOK_URLS via legacy helper if no JSON routes yet
    if not destinations:
        webhook_client.send_alert_webhook(payload, urls=None, secret=None)
    else:
        for wh in destinations:
            webhook_client.send_alert_webhook(
                payload,
                urls=[wh["url"]],
                secret=wh.get("secret") or None,
            )

    payload_for_ui = {
        **payload,
        "delivered_to": [d.get("name") or d.get("url") for d in destinations]
        or (webhook_client.configured_urls() or ["(env WEBHOOK_URLS / none)"]),
    }
    _remember(payload_for_ui)
    logger.info(
        "Alert fired id=%s type=%s rule=%s latency_ms=%.1f destinations=%s",
        payload["alert_id"],
        event_type,
        rule.get("name"),
        latency_ms,
        len(destinations) or "env-fallback",
    )


def emit_from_event(job_id: str, event: Dict[str, Any], job: Optional[Dict[str, Any]] = None) -> int:
    """
    Evaluate rules for one detection event and enqueue matching alerts.
    Returns number of alerts queued. Never raises into the analysis loop.
    """
    try:
        if job is None:
            try:
                import analysis_pipeline
                job = analysis_pipeline.get_job(job_id)
            except Exception:
                job = None

        matches = _matching_rules(event, job)
        if not matches:
            # Still allow legacy env webhook for every event when no rules match
            # only if there are zero enabled rules configured - otherwise silence.
            rules = alert_config.list_rules()
            if not any(r.get("enabled", True) for r in rules):
                webhook_client.send_event_webhook(job_id, event)
            return 0

        queued = 0
        for item in matches:
            rule = item["rule"]
            label = str(event.get("plate_number") or event.get("label") or event.get("type") or "")
            key = _cooldown_key(job_id, str(rule.get("id")), str(event.get("type")), label)
            cooldown = float(rule.get("cooldown_seconds") or 5.0)
            if _under_cooldown(key, cooldown):
                continue

            queued_at = time.perf_counter()
            _executor.submit(
                _deliver_alert,
                job_id=job_id,
                job=job,
                event=event,
                rule=rule,
                watchlist_match=item.get("watchlist_match"),
                queued_at=queued_at,
            )
            queued += 1
        return queued
    except Exception:
        logger.exception("emit_from_event failed job_id=%s", job_id)
        return 0


def test_webhook(url: Optional[str] = None) -> Dict[str, Any]:
    """Send a synthetic alert so operators can verify their endpoint."""
    payload = {
        "alert_id": uuid.uuid4().hex[:16],
        "kind": "netra.alert.test",
        "severity": "low",
        "latency_ms": 0,
        "fired_at": _utc_now(),
        "rule": {"id": "test", "name": "Webhook connectivity test"},
        "watchlist_match": None,
        "stream": {"job_id": "test", "source": "test", "original_name": "test"},
        "event": {
            "event_id": "test",
            "type": "weapon",
            "label": "test-alert",
            "confidence": 0.99,
            "video_timestamp": "00:00:00.000",
        },
        "context": {
            "snapshot_url": None,
            "clip_url": None,
        },
        "message": "N.E.T.R.A alerting pipeline connectivity check.",
    }
    urls = [url] if url else None
    webhook_client.send_alert_webhook(payload, urls=urls, secret=None)
    return {"ok": True, "payload": payload}
