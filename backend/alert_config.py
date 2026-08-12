"""

alert_config.py

---------------

Persistent store for alerting rules, watchlists, and webhook routes.



Saved to ALERT_CONFIG_PATH (default ./alert_config.json) so operators can

tune routing without redeploying. Env WEBHOOK_URLS still work as a bootstrap

fallback when no routes are configured in the JSON file.

"""



from __future__ import annotations



import json

import logging

import os

import threading

import uuid

from copy import deepcopy

from typing import Any, Dict, List, Optional



logger = logging.getLogger("netra.alert_config")



CONFIG_PATH = os.getenv(

    "ALERT_CONFIG_PATH",

    os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_config.json"),

)



_lock = threading.RLock()

_config: Dict[str, Any] = {}





def _new_id() -> str:

    return uuid.uuid4().hex[:12]





def _default_config() -> Dict[str, Any]:

    """

    Sensible out-of-box rules: alert on weapon / violence / anomaly.
    Plate stays off Alert unless an operator enables a plate rule.
    Webhook URLs come from env until saved in the UI.

    """

    env_urls = [

        u.strip()

        for u in os.getenv("WEBHOOK_URLS", "").split(",")

        if u.strip()

    ]

    webhooks = [

        {

            "id": _new_id(),

            "name": f"Webhook {i + 1}",

            "url": url,

            "enabled": True,

            "secret": os.getenv("WEBHOOK_SECRET", ""),

            "event_types": ["weapon", "violence", "anomaly"],

            "min_confidence": 0.0,

        }

        for i, url in enumerate(env_urls)

    ]



    return {

        "version": 1,

        "public_base_url": os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000"),

        "webhooks": webhooks,

        "watchlists": [

            {

                "id": _new_id(),

                "name": "Priority plates",

                "type": "plate",

                "enabled": True,

                "values": [],

                "notes": "Add plate numbers (A-Z0-9). Matching plate events always alert.",

            }

        ],

        "rules": [

            {

                "id": _new_id(),

                "name": "Weapon detected",

                "enabled": True,

                "event_types": ["weapon"],

                "min_confidence": 0.65,

                "watchlist_id": None,

                "severity_ids": [],

                "severity_labels": [],

                "cooldown_seconds": 5.0,

                "severity": "critical",

                "include_clip": True,

            },

            {

                "id": _new_id(),

                "name": "Violence / fight",

                "enabled": True,

                "event_types": ["violence"],

                "min_confidence": 0.90,

                "watchlist_id": None,

                "cooldown_seconds": 5.0,

                "severity": "critical",

                "include_clip": True,

            },

            {

                "id": _new_id(),

                "name": "Anomaly",

                "enabled": True,

                "event_types": ["anomaly"],

                "min_confidence": 0.0,

                "watchlist_id": None,

                "cooldown_seconds": 8.0,

                "severity": "high",

                "include_clip": False,

            },

            {

                "id": _new_id(),

                "name": "Watchlist plate match",

                "enabled": False,

                "event_types": ["plate"],

                "min_confidence": 0.30,

                "watchlist_id": "any_plate",

                "cooldown_seconds": 5.0,

                "severity": "high",

                "include_clip": True,

            },

        ],

    }





def _ensure_loaded() -> None:

    global _config

    with _lock:

        if _config:

            return

        if os.path.isfile(CONFIG_PATH):

            try:

                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:

                    _config = json.load(fh)

                logger.info("Loaded alert config from %s", CONFIG_PATH)

                return

            except Exception:

                logger.exception("Failed to read %s - using defaults", CONFIG_PATH)

        _config = _default_config()

        _save_unlocked()





def _save_unlocked() -> None:

    parent = os.path.dirname(CONFIG_PATH)

    if parent:

        os.makedirs(parent, exist_ok=True)

    tmp = CONFIG_PATH + ".tmp"

    with open(tmp, "w", encoding="utf-8") as fh:

        json.dump(_config, fh, indent=2)

    os.replace(tmp, CONFIG_PATH)





def get_config() -> Dict[str, Any]:

    _ensure_loaded()

    with _lock:

        return deepcopy(_config)





def save_config(new_config: Dict[str, Any]) -> Dict[str, Any]:

    global _config

    _ensure_loaded()

    with _lock:

        merged = deepcopy(_config)

        for key in ("webhooks", "watchlists", "rules", "public_base_url"):

            if key in new_config:

                merged[key] = new_config[key]

        merged["version"] = int(merged.get("version") or 1) + 1

        _config = merged

        _save_unlocked()

        return deepcopy(_config)





def list_webhooks() -> List[Dict[str, Any]]:

    return get_config().get("webhooks") or []





def list_watchlists() -> List[Dict[str, Any]]:

    return get_config().get("watchlists") or []





def list_rules() -> List[Dict[str, Any]]:

    return get_config().get("rules") or []





def public_base_url() -> str:

    cfg = get_config()

    return str(cfg.get("public_base_url") or "http://127.0.0.1:8000").rstrip("/")





def upsert_webhook(item: Dict[str, Any]) -> Dict[str, Any]:

    cfg = get_config()

    webhooks = cfg.get("webhooks") or []

    wid = item.get("id") or _new_id()

    item = {**item, "id": wid}

    replaced = False

    for i, existing in enumerate(webhooks):

        if existing.get("id") == wid:

            webhooks[i] = {**existing, **item}

            replaced = True

            break

    if not replaced:

        webhooks.append(item)

    return save_config({"webhooks": webhooks})





def delete_webhook(webhook_id: str) -> Dict[str, Any]:

    cfg = get_config()

    webhooks = [w for w in (cfg.get("webhooks") or []) if w.get("id") != webhook_id]

    return save_config({"webhooks": webhooks})





def upsert_watchlist(item: Dict[str, Any]) -> Dict[str, Any]:

    cfg = get_config()

    lists = cfg.get("watchlists") or []

    wid = item.get("id") or _new_id()

    values = item.get("values") or []

    # Normalize plate-like values

    if (item.get("type") or "plate") == "plate":

        values = [

            "".join(ch for ch in str(v).upper() if ch.isalnum())

            for v in values

            if str(v).strip()

        ]

    item = {**item, "id": wid, "values": values}

    replaced = False

    for i, existing in enumerate(lists):

        if existing.get("id") == wid:

            lists[i] = {**existing, **item}

            replaced = True

            break

    if not replaced:

        lists.append(item)

    return save_config({"watchlists": lists})





def delete_watchlist(watchlist_id: str) -> Dict[str, Any]:

    cfg = get_config()

    lists = [w for w in (cfg.get("watchlists") or []) if w.get("id") != watchlist_id]

    return save_config({"watchlists": lists})





def upsert_rule(item: Dict[str, Any]) -> Dict[str, Any]:

    cfg = get_config()

    rules = cfg.get("rules") or []

    rid = item.get("id") or _new_id()

    item = {**item, "id": rid}

    replaced = False

    for i, existing in enumerate(rules):

        if existing.get("id") == rid:

            rules[i] = {**existing, **item}

            replaced = True

            break

    if not replaced:

        rules.append(item)

    return save_config({"rules": rules})





def delete_rule(rule_id: str) -> Dict[str, Any]:

    cfg = get_config()

    rules = [r for r in (cfg.get("rules") or []) if r.get("id") != rule_id]

    return save_config({"rules": rules})





def match_watchlist(

    event: Dict[str, Any],

    watchlist_id: Optional[str],

) -> Optional[Dict[str, Any]]:

    """

    Return the matching watchlist entry when the event label/plate hits

    a configured value. watchlist_id may be a concrete id or "any_plate".

    """

    event_type = (event.get("type") or "").lower()

    if event_type != "plate":

        return None



    plate = (

        event.get("plate_number")

        or event.get("text")

        or event.get("label")

        or ""

    )

    plate_key = "".join(ch for ch in str(plate).upper() if ch.isalnum())

    if not plate_key:

        return None



    for wl in list_watchlists():

        if not wl.get("enabled", True):

            continue

        if (wl.get("type") or "plate") != "plate":

            continue

        if watchlist_id and watchlist_id != "any_plate" and wl.get("id") != watchlist_id:

            continue

        values = wl.get("values") or []

        if not values:

            continue

        if plate_key in values:

            return {

                "watchlist_id": wl.get("id"),

                "watchlist_name": wl.get("name"),

                "matched_value": plate_key,

            }

    return None

