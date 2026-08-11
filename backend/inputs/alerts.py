"""
inputs/alerts.py
----------------
REST API for the Sub-5s alerting pipeline:
  - rules / watchlists / webhook routes
  - recent alerts
  - webhook connectivity test
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import alert_config
import alerting

router = APIRouter(tags=["alerts"])


class WebhookIn(BaseModel):
    id: Optional[str] = None
    name: str = "Operator webhook"
    url: str
    enabled: bool = True
    secret: str = ""
    event_types: List[str] = Field(
        default_factory=lambda: ["weapon", "violence", "plate", "anomaly"]
    )
    min_confidence: float = 0.0


class WatchlistIn(BaseModel):
    id: Optional[str] = None
    name: str = "Watchlist"
    type: str = "plate"
    enabled: bool = True
    values: List[str] = Field(default_factory=list)
    notes: str = ""


class RuleIn(BaseModel):
    id: Optional[str] = None
    name: str = "Rule"
    enabled: bool = True
    event_types: List[str] = Field(default_factory=lambda: ["weapon"])
    min_confidence: float = 0.0
    watchlist_id: Optional[str] = None
    stream_ids: List[str] = Field(default_factory=list)
    stream_labels: List[str] = Field(default_factory=list)
    cooldown_seconds: float = 5.0
    severity: str = "high"
    include_clip: bool = True
    require_watchlist: bool = False


class ConfigIn(BaseModel):
    public_base_url: Optional[str] = None
    webhooks: Optional[List[Dict[str, Any]]] = None
    watchlists: Optional[List[Dict[str, Any]]] = None
    rules: Optional[List[Dict[str, Any]]] = None


class TestWebhookIn(BaseModel):
    url: Optional[str] = None


@router.get("/alerts/config")
def get_alert_config():
    return alert_config.get_config()


@router.put("/alerts/config")
def put_alert_config(body: ConfigIn):
    return alert_config.save_config(body.model_dump(exclude_none=True))


@router.get("/alerts/recent")
def recent_alerts(limit: int = 50):
    return {"alerts": alerting.list_recent_alerts(limit=limit)}


@router.post("/alerts/webhooks")
def upsert_webhook(body: WebhookIn):
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    return alert_config.upsert_webhook(body.model_dump())


@router.delete("/alerts/webhooks/{webhook_id}")
def remove_webhook(webhook_id: str):
    return alert_config.delete_webhook(webhook_id)


@router.post("/alerts/watchlists")
def upsert_watchlist(body: WatchlistIn):
    return alert_config.upsert_watchlist(body.model_dump())


@router.delete("/alerts/watchlists/{watchlist_id}")
def remove_watchlist(watchlist_id: str):
    return alert_config.delete_watchlist(watchlist_id)


@router.post("/alerts/rules")
def upsert_rule(body: RuleIn):
    return alert_config.upsert_rule(body.model_dump())


@router.delete("/alerts/rules/{rule_id}")
def remove_rule(rule_id: str):
    return alert_config.delete_rule(rule_id)


@router.post("/alerts/test-webhook")
def test_webhook(body: TestWebhookIn = TestWebhookIn()):
    return alerting.test_webhook(url=body.url)
