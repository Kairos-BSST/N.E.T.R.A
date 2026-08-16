from __future__ import annotations
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import alert_config
import alerting
import database
from auth import administrator, current_user

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
def get_alert_config(user=Depends(current_user)):
    return alert_config.get_config()


@router.put("/alerts/config")
def put_alert_config(body: ConfigIn, user=Depends(administrator)):
    result = alert_config.save_config(body.model_dump(exclude_none=True))
    database.record_audit(user["id"], "ALERT_CONFIG_UPDATED", resource_type="alert_config")
    return result


@router.get("/alerts/recent")
def recent_alerts(limit: int = 50, job_id: Optional[str] = None, user=Depends(current_user)):
    if job_id:
        job = database.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Analysis job not found.")
        if user.get("role") != "administrator" and job.get("user_id") != user.get("id"):
            raise HTTPException(status_code=403, detail="You do not have access to this scan.")
    return {"alerts": alerting.list_recent_alerts(limit=limit, job_id=job_id)}


@router.post("/alerts/webhooks")
def upsert_webhook(body: WebhookIn, user=Depends(administrator)):
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    result = alert_config.upsert_webhook(body.model_dump())
    database.record_audit(user["id"], "WEBHOOK_UPSERTED", resource_type="webhook", resource_id=result.get("id"))
    return result


@router.delete("/alerts/webhooks/{webhook_id}")
def remove_webhook(webhook_id: str, user=Depends(administrator)):
    result = alert_config.delete_webhook(webhook_id)
    database.record_audit(user["id"], "WEBHOOK_DELETED", resource_type="webhook", resource_id=webhook_id)
    return result


@router.post("/alerts/watchlists")
def upsert_watchlist(body: WatchlistIn, user=Depends(administrator)):
    result = alert_config.upsert_watchlist(body.model_dump())
    database.record_audit(user["id"], "WATCHLIST_UPSERTED", resource_type="watchlist", resource_id=result.get("id"))
    return result


@router.delete("/alerts/watchlists/{watchlist_id}")
def remove_watchlist(watchlist_id: str, user=Depends(administrator)):
    result = alert_config.delete_watchlist(watchlist_id)
    database.record_audit(user["id"], "WATCHLIST_DELETED", resource_type="watchlist", resource_id=watchlist_id)
    return result


@router.post("/alerts/rules")
def upsert_rule(body: RuleIn, user=Depends(administrator)):
    result = alert_config.upsert_rule(body.model_dump())
    database.record_audit(user["id"], "ALERT_RULE_UPSERTED", resource_type="rule", resource_id=result.get("id"))
    return result


@router.delete("/alerts/rules/{rule_id}")
def remove_rule(rule_id: str, user=Depends(administrator)):
    result = alert_config.delete_rule(rule_id)
    database.record_audit(user["id"], "ALERT_RULE_DELETED", resource_type="rule", resource_id=rule_id)
    return result


@router.post("/alerts/test-webhook")
def test_webhook(body: TestWebhookIn = TestWebhookIn(), user=Depends(administrator)):
    result = alerting.test_webhook(url=body.url)
    database.record_audit(user["id"], "WEBHOOK_TESTED", resource_type="webhook")
    return result
