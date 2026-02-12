import copy
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from rapidfuzz import fuzz, process

try:
    import redis
except Exception:
    redis = None

# Load Environment Variables
load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Vapi-Restaurant")

app = FastAPI(title="Vapi Restaurant Backend")

# --- Configuration ---
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_uUTzYB6Awd3q@ep-withered-pond-aibgxrga-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require",
)

if DB_URL.startswith("postgresql+asyncpg://"):
    DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    if "?ssl" in DB_URL:
        DB_URL = DB_URL.replace("?ssl", "?sslmode=require")

CURRENT_RESTAURANT_ID = int(os.getenv("DEFAULT_RESTAURANT_ID", "1"))
DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "tenant_default")
MAX_WEBHOOK_EVENTS_PER_MINUTE = int(os.getenv("MAX_WEBHOOK_EVENTS_PER_MINUTE", "120"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "21600"))
VAPI_WEBHOOK_SECRET = os.getenv("VAPI_WEBHOOK_SECRET", "")
REDIS_URL = os.getenv("REDIS_URL", "")

# --- In-memory caches (MVP + runtime mirror) ---
ITEMS_DB: Dict[str, Dict[str, Any]] = {}
SEARCH_INDEX: List[Tuple[str, str]] = []
RESTAURANT_INFO: Dict[str, Any] = {"tax_rate": 0.13}
RUNTIME_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
MENU_CACHE: Dict[int, Dict[str, Any]] = {}

# --- Session Management (still in-memory; should move to Redis later) ---
sessions: Dict[str, Dict[str, Any]] = {}
rate_limit_memory: Dict[str, Tuple[int, float]] = {}
redis_client = redis.from_url(REDIS_URL, decode_responses=True) if (redis and REDIS_URL) else None


class NLCommandRequest(BaseModel):
    text: str
    tenant_id: str = DEFAULT_TENANT_ID
    restaurant_id: int = CURRENT_RESTAURANT_ID
    actor_id: str = "owner"
    source: str = "chat"
    language: Optional[str] = None
    confirm: bool = False
    dry_run: bool = False


class NLConfirmRequest(BaseModel):
    intent_id: str
    actor_id: str = "owner"


class NLUndoRequest(BaseModel):
    tenant_id: str = DEFAULT_TENANT_ID
    restaurant_id: int = CURRENT_RESTAURANT_ID
    actor_id: str = "owner"
    source: str = "chat"
    change_id: Optional[str] = None


def get_db_connection():
    # Sanitize URL for psycopg2
    sanitized_url = DB_URL.split("?")[0]
    return psycopg2.connect(sanitized_url, sslmode="require")


def ensure_saas_tables() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS nl_intents (
        intent_id VARCHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        restaurant_id INTEGER NOT NULL,
        actor_id VARCHAR(64),
        source VARCHAR(32),
        language VARCHAR(8),
        raw_text TEXT NOT NULL,
        intent_type VARCHAR(128) NOT NULL,
        confidence NUMERIC(4,3) NOT NULL,
        risk_level VARCHAR(16) NOT NULL,
        requires_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
        effective_start TIMESTAMPTZ,
        effective_end TIMESTAMPTZ,
        payload JSONB NOT NULL,
        validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
        status VARCHAR(32) NOT NULL DEFAULT 'parsed',
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS config_changes (
        change_id VARCHAR(64) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        restaurant_id INTEGER NOT NULL,
        intent_id VARCHAR(64) REFERENCES nl_intents(intent_id),
        action_type VARCHAR(128) NOT NULL,
        payload JSONB NOT NULL,
        previous_state JSONB NOT NULL,
        new_state JSONB NOT NULL,
        applied BOOLEAN NOT NULL DEFAULT TRUE,
        applied_at TIMESTAMPTZ,
        rolled_back BOOLEAN NOT NULL DEFAULT FALSE,
        rolled_back_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS config_snapshots (
        snapshot_id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        restaurant_id INTEGER NOT NULL,
        config JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        audit_id BIGSERIAL PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        restaurant_id INTEGER NOT NULL,
        actor_id VARCHAR(64),
        source VARCHAR(32),
        event_type VARCHAR(64) NOT NULL,
        detail JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS webhook_events (
        event_id VARCHAR(128) PRIMARY KEY,
        call_id VARCHAR(128),
        tool_name VARCHAR(128),
        response TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    ALTER TABLE orders
      ADD COLUMN IF NOT EXISTS source_event_id VARCHAR(128);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_source_event
      ON orders (source_event_id);

    CREATE INDEX IF NOT EXISTS idx_nl_intents_tenant_rest_created
      ON nl_intents (tenant_id, restaurant_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_config_changes_tenant_rest_created
      ON config_changes (tenant_id, restaurant_id, created_at DESC);
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def load_data_from_db() -> None:
    global ITEMS_DB, SEARCH_INDEX, RESTAURANT_INFO
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT name FROM restaurants WHERE id = %s", (CURRENT_RESTAURANT_ID,))
        res = cur.fetchone()
        if res:
            RESTAURANT_INFO["name_en"] = res["name"]
            logger.info("Loaded Restaurant: %s", res["name"])

        cur.execute("SELECT * FROM menu_items WHERE restaurant_id = %s", (CURRENT_RESTAURANT_ID,))
        rows = cur.fetchall()

        new_items_db: Dict[str, Dict[str, Any]] = {}
        new_search_index: List[Tuple[str, str]] = []

        for row in rows:
            row["price"] = float(row["price"])
            new_items_db[row["id"]] = row
            keywords = row.get("keywords") or []
            search_text = f"{row['name_en']} {row['name_zh']} {' '.join(keywords)}".lower()
            new_search_index.append((search_text, row["id"]))

        ITEMS_DB = new_items_db
        SEARCH_INDEX = new_search_index
        logger.info("Loaded %s menu items from DB.", len(ITEMS_DB))

        cur.close()
        conn.close()
    except Exception as exc:
        logger.error("DB Load Error: %s", exc)


def get_menu_cache(restaurant_id: int) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str]]]:
    if restaurant_id in MENU_CACHE:
        return MENU_CACHE[restaurant_id]["items"], MENU_CACHE[restaurant_id]["search_index"]

    items_db: Dict[str, Dict[str, Any]] = {}
    search_index: List[Tuple[str, str]] = []
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM menu_items WHERE restaurant_id = %s", (restaurant_id,))
            rows = cur.fetchall()
        for row in rows:
            row["price"] = float(row["price"])
            items_db[row["id"]] = row
            keywords = row.get("keywords") or []
            search_text = f"{row['name_en']} {row['name_zh']} {' '.join(keywords)}".lower()
            search_index.append((search_text, row["id"]))
    finally:
        conn.close()
    MENU_CACHE[restaurant_id] = {"items": items_db, "search_index": search_index}
    return items_db, search_index


def get_session_store_key(tenant_id: str, restaurant_id: int, call_id: str) -> str:
    return f"session:{tenant_id}:{restaurant_id}:{call_id}"


def get_session(call_id: str, tenant_id: str, restaurant_id: int) -> Dict[str, Any]:
    if not call_id:
        return {}
    key = get_session_store_key(tenant_id, restaurant_id, call_id)

    if redis_client:
        raw = redis_client.get(key)
        if raw:
            return json.loads(raw)

    if key not in sessions:
        sessions[key] = {
            "cart": [],
            "fulfillment": {},
            "lang": "en",
            "tenant_id": tenant_id,
            "restaurant_id": restaurant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return sessions[key]


def save_session(call_id: str, tenant_id: str, restaurant_id: int, session: Dict[str, Any]) -> None:
    key = get_session_store_key(tenant_id, restaurant_id, call_id)
    sessions[key] = session
    if redis_client:
        redis_client.setex(key, SESSION_TTL_SECONDS, json.dumps(session))


# --- Helper Functions ---
def format_price(amount: float) -> str:
    return f"${amount:.2f}"


def calculate_totals(cart: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    subtotal = sum(item["price"] * item["qty"] for item in cart)
    tax = subtotal * RESTAURANT_INFO.get("tax_rate", 0.13)
    total = subtotal + tax
    return subtotal, tax, total


def save_order_to_db(
    order_id: str,
    restaurant_id: int,
    cart: List[Dict[str, Any]],
    total: float,
    phone: str = "Unknown",
    source_event_id: Optional[str] = None,
) -> bool:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (id, restaurant_id, customer_phone, items, total, status, source_event_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_event_id) DO NOTHING
                """,
                (
                    order_id,
                    restaurant_id,
                    phone,
                    json.dumps(cart),
                    total,
                    "confirmed",
                    source_event_id,
                ),
            )
            inserted = cur.rowcount > 0
        conn.commit()
        conn.close()
        logger.info("Order %s saved to DB=%s.", order_id, inserted)
        return inserted
    except Exception as exc:
        logger.error("Failed to save order to DB: %s", exc)
        return False


def detect_language(text: str, hint: Optional[str]) -> str:
    if hint in {"zh", "en"}:
        return hint
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "en"


def normalize_phone(raw: str) -> Optional[str]:
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.strip().startswith("+"):
        return f"+{digits}"
    return None


def extract_phone(text: str) -> Optional[str]:
    match = re.search(r"(\+?\d[\d\-\s\(\)]{7,}\d)", text)
    if not match:
        return None
    return normalize_phone(match.group(1))


def parse_time_range(text: str) -> Optional[Tuple[str, str]]:
    m = re.search(r"(\d{1,2})\s*(?:点|:)?\s*(?:到|-|to)\s*(\d{1,2})", text, re.IGNORECASE)
    if not m:
        return None
    start_hour = int(m.group(1))
    end_hour = int(m.group(2))
    if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
        return f"{start_hour:02d}:00", f"{end_hour:02d}:00"
    return None


def find_best_item(text: str) -> Tuple[Optional[Dict[str, Any]], float]:
    if not SEARCH_INDEX:
        return None, 0.0
    matches = process.extract(text.lower(), [x[0] for x in SEARCH_INDEX], scorer=fuzz.partial_ratio, limit=1)
    if not matches:
        return None, 0.0
    idx = matches[0][2]
    score = float(matches[0][1]) / 100.0
    item_id = SEARCH_INDEX[idx][1]
    return ITEMS_DB.get(item_id), score


def to_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str]) -> None:
    if not VAPI_WEBHOOK_SECRET:
        return
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing webhook signature")

    computed = hmac.new(VAPI_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.strip()
    if received.startswith("sha256="):
        received = received.split("=", 1)[1]
    if not hmac.compare_digest(computed, received):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def resolve_webhook_context(request: Request, message: Dict[str, Any]) -> Tuple[str, int]:
    tenant_id = request.headers.get("x-tenant-id") or DEFAULT_TENANT_ID
    header_restaurant_id = request.headers.get("x-restaurant-id")
    restaurant_id = CURRENT_RESTAURANT_ID

    if header_restaurant_id and header_restaurant_id.isdigit():
        restaurant_id = int(header_restaurant_id)
    else:
        call_obj = message.get("call", {}) or {}
        metadata = call_obj.get("metadata", {}) or {}
        rid = metadata.get("restaurant_id")
        if isinstance(rid, int):
            restaurant_id = rid
        elif isinstance(rid, str) and rid.isdigit():
            restaurant_id = int(rid)
    return tenant_id, restaurant_id


def enforce_webhook_rate_limit(call_id: str) -> None:
    key = f"rate:{call_id or 'unknown'}"
    now = time.time()

    if redis_client:
        current = redis_client.incr(key)
        if current == 1:
            redis_client.expire(key, 60)
        if current > MAX_WEBHOOK_EVENTS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        return

    count, start = rate_limit_memory.get(key, (0, now))
    if now - start > 60:
        count, start = 0, now
    count += 1
    rate_limit_memory[key] = (count, start)
    if count > MAX_WEBHOOK_EVENTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def get_stored_webhook_response(conn, event_id: str) -> Optional[str]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT response FROM webhook_events WHERE event_id = %s LIMIT 1", (event_id,))
        row = cur.fetchone()
    return row["response"] if row else None


def store_webhook_response(conn, event_id: str, call_id: str, tool_name: str, response: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO webhook_events (event_id, call_id, tool_name, response)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (event_id, call_id, tool_name, response),
        )


def get_default_runtime_config() -> Dict[str, Any]:
    return {
        "transfer_rules": [],
        "handoff_policy": {
            "user_requests_human": True,
            "busy_line_policy": "transfer",
            "default_number": os.getenv("TRANSFER_PHONE_NUMBER", "+15550000000"),
        },
        "business_hours": {
            "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "open_time": "10:00",
            "close_time": "22:00",
            "timezone": "America/Toronto",
        },
        "menu_overrides": {},
        "recommendation_weights": {},
    }


def cache_key(tenant_id: str, restaurant_id: int) -> str:
    return f"{tenant_id}:{restaurant_id}"


def get_runtime_config(conn, tenant_id: str, restaurant_id: int) -> Dict[str, Any]:
    key = cache_key(tenant_id, restaurant_id)
    if key in RUNTIME_CONFIG_CACHE:
        return copy.deepcopy(RUNTIME_CONFIG_CACHE[key])

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT config FROM config_snapshots
            WHERE tenant_id = %s AND restaurant_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tenant_id, restaurant_id),
        )
        row = cur.fetchone()

    cfg = row["config"] if row else get_default_runtime_config()
    RUNTIME_CONFIG_CACHE[key] = cfg
    return copy.deepcopy(cfg)


def persist_config_snapshot(conn, tenant_id: str, restaurant_id: int, config: Dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO config_snapshots (tenant_id, restaurant_id, config)
            VALUES (%s, %s, %s)
            """,
            (tenant_id, restaurant_id, json.dumps(config)),
        )
    RUNTIME_CONFIG_CACHE[cache_key(tenant_id, restaurant_id)] = copy.deepcopy(config)


def insert_audit_log(
    conn,
    tenant_id: str,
    restaurant_id: int,
    actor_id: str,
    source: str,
    event_type: str,
    detail: Dict[str, Any],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (tenant_id, restaurant_id, actor_id, source, event_type, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (tenant_id, restaurant_id, actor_id, source, event_type, json.dumps(detail)),
        )


def build_summary(intent_type: str, payload: Dict[str, Any], lang: str) -> str:
    if intent_type == "routing.transfer_rule.upsert":
        phone = payload.get("phone_number", "unknown")
        trigger = payload.get("trigger", "always")
        return (
            f"从现在起按 {trigger} 规则转接到 {phone}。"
            if lang == "zh"
            else f"Calls will transfer to {phone} with trigger={trigger}."
        )
    if intent_type == "hours.business_hours.set":
        return (
            f"营业时间更新为 {payload.get('open_time')} - {payload.get('close_time')}。"
            if lang == "zh"
            else f"Business hours updated to {payload.get('open_time')} - {payload.get('close_time')}."
        )
    if intent_type == "menu.item.availability.set":
        item_name = payload.get("item_ref", {}).get("name", "item")
        available = payload.get("available", True)
        return (
            f"菜品 {item_name} 已{'上架' if available else '下架'}。"
            if lang == "zh"
            else f"Item {item_name} is now {'available' if available else 'unavailable'}."
        )
    if intent_type == "menu.item.price.set":
        item_name = payload.get("item_ref", {}).get("name", "item")
        new_price = payload.get("new_price")
        return (
            f"菜品 {item_name} 价格已改为 {new_price}。"
            if lang == "zh"
            else f"Updated {item_name} price to {new_price}."
        )
    if intent_type == "ops.undo":
        return "已回滚最近一次配置。" if lang == "zh" else "Rolled back the most recent config change."
    if intent_type == "order.query":
        return "已返回订单查询结果。" if lang == "zh" else "Order query results ready."
    return "配置已处理。" if lang == "zh" else "Command processed."


def parse_nl_command(req: NLCommandRequest) -> Dict[str, Any]:
    text = req.text.strip()
    lang = detect_language(text, req.language)
    lower = text.lower()
    payload: Dict[str, Any] = {}
    validation_errors: List[str] = []
    confidence = 0.8
    risk_level = "low"
    requires_confirmation = False
    intent_type = "unknown"

    if any(k in text for k in ["撤回", "回滚"]) or "undo" in lower:
        intent_type = "ops.undo"
        confidence = 0.98

    elif any(k in text for k in ["订单", "查单"]) or "order" in lower:
        intent_type = "order.query"
        confidence = 0.9
        status = ["confirmed"]
        if any(k in text for k in ["没确认", "未确认", "pending"]) or "unconfirmed" in lower:
            status = ["pending"]
        payload = {
            "filters": {
                "status": status,
                "from": None,
                "to": None,
                "has_transfer": True if "转人工" in text else None,
            },
            "aggregation": "count" if any(k in text for k in ["多少", "几", "count"]) else "list",
            "limit": 20,
        }

    elif ("转接" in text or "transfer" in lower) and extract_phone(text):
        intent_type = "routing.transfer_rule.upsert"
        risk_level = "high"
        requires_confirmation = True
        confidence = 0.94
        payload = {
            "trigger": "after_hours" if ("后" in text or "after" in lower) else "always",
            "phone_number": extract_phone(text),
            "priority": 100,
            "conditions": {"language": "any", "min_order_amount": None},
        }

    elif "营业时间" in text or "business hours" in lower or "hours" in lower:
        intent_type = "hours.business_hours.set"
        risk_level = "high"
        requires_confirmation = True
        tr = parse_time_range(text)
        confidence = 0.92 if tr else 0.7
        if not tr:
            validation_errors.append("Could not parse business hour range")
        else:
            payload = {
                "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "open_time": tr[0],
                "close_time": tr[1],
                "timezone": "America/Toronto",
            }

    elif any(k in text for k in ["暂停", "下架", "恢复", "上架"]) or any(k in lower for k in ["pause", "resume", "available"]):
        intent_type = "menu.item.availability.set"
        risk_level = "medium"
        item, item_conf = find_best_item(text)
        confidence = max(0.6, min(0.95, item_conf))
        if not item:
            validation_errors.append("No menu item matched")
        else:
            payload = {
                "item_ref": {"id": item["id"], "name": item["name_zh"] if lang == "zh" else item["name_en"]},
                "available": not any(k in text for k in ["暂停", "下架"]) and "pause" not in lower,
                "effective_until": None,
                "reason": "sold_out" if any(k in text for k in ["暂停", "下架"]) else "manual_update",
            }
        requires_confirmation = confidence < 0.9

    elif "价格" in text or "price" in lower:
        intent_type = "menu.item.price.set"
        risk_level = "high"
        requires_confirmation = True
        item, item_conf = find_best_item(text)
        price_match = re.search(r"(\d+(?:\.\d{1,2})?)", text)
        confidence = 0.93 if (item and price_match) else 0.65
        if not item:
            validation_errors.append("No menu item matched for price update")
        if not price_match:
            validation_errors.append("No price value found")
        if item and price_match:
            payload = {
                "item_ref": {"id": item["id"], "name": item["name_zh"] if lang == "zh" else item["name_en"]},
                "new_price": float(price_match.group(1)),
                "currency": "CAD",
                "effective_at": "immediate",
            }

    elif "推荐" in text or "recommend" in lower:
        intent_type = "menu.item.recommendation_weight.set"
        risk_level = "low"
        confidence = 0.88
        item, _ = find_best_item(text)
        payload = {
            "item_ref": {"id": item["id"], "name": item["name_zh"] if lang == "zh" else item["name_en"]} if item else None,
            "weight": "high",
            "effective_at": "immediate",
        }

    else:
        intent_type = "clarification_needed"
        confidence = 0.4
        validation_errors.append("Command not recognized")

    effective_start = to_iso_now()
    intent = {
        "intent_id": f"int_{uuid.uuid4().hex[:18]}",
        "tenant_id": req.tenant_id,
        "restaurant_id": req.restaurant_id,
        "actor_id": req.actor_id,
        "source": req.source,
        "language": lang,
        "raw_text": text,
        "intent_type": intent_type,
        "confidence": round(confidence, 3),
        "requires_confirmation": requires_confirmation,
        "risk_level": risk_level,
        "effective_window": {
            "start_at": effective_start,
            "end_at": None,
            "timezone": "America/Toronto",
        },
        "payload": payload,
        "validation_errors": validation_errors,
    }
    return intent


def insert_intent(conn, intent: Dict[str, Any], status: str = "parsed") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO nl_intents (
                intent_id, tenant_id, restaurant_id, actor_id, source, language, raw_text,
                intent_type, confidence, risk_level, requires_confirmation,
                effective_start, effective_end, payload, validation_errors, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                intent["intent_id"],
                intent["tenant_id"],
                intent["restaurant_id"],
                intent["actor_id"],
                intent["source"],
                intent["language"],
                intent["raw_text"],
                intent["intent_type"],
                intent["confidence"],
                intent["risk_level"],
                intent["requires_confirmation"],
                intent["effective_window"]["start_at"],
                intent["effective_window"]["end_at"],
                json.dumps(intent["payload"]),
                json.dumps(intent["validation_errors"]),
                status,
            ),
        )


def update_intent_status(conn, intent_id: str, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE nl_intents SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE intent_id = %s",
            (status, intent_id),
        )


def apply_intent_to_config(current: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    new_cfg = copy.deepcopy(current)
    intent_type = intent["intent_type"]
    payload = intent["payload"]

    if intent_type == "routing.transfer_rule.upsert":
        rules = new_cfg.setdefault("transfer_rules", [])
        rules.append(
            {
                "rule_id": f"rule_{uuid.uuid4().hex[:10]}",
                "trigger": payload.get("trigger", "always"),
                "phone_number": payload.get("phone_number"),
                "priority": payload.get("priority", 100),
                "conditions": payload.get("conditions", {}),
            }
        )

    elif intent_type == "routing.transfer_rule.delete":
        new_cfg["transfer_rules"] = []

    elif intent_type == "routing.handoff_policy.set":
        policy = new_cfg.setdefault("handoff_policy", {})
        policy.update(payload)

    elif intent_type == "hours.business_hours.set":
        new_cfg["business_hours"] = payload

    elif intent_type == "menu.item.availability.set":
        item_id = payload.get("item_ref", {}).get("id")
        if item_id:
            menu_overrides = new_cfg.setdefault("menu_overrides", {})
            row = menu_overrides.setdefault(item_id, {})
            row["available"] = payload.get("available", True)
            if payload.get("reason"):
                row["reason"] = payload.get("reason")

    elif intent_type == "menu.item.price.set":
        item_id = payload.get("item_ref", {}).get("id")
        if item_id:
            menu_overrides = new_cfg.setdefault("menu_overrides", {})
            row = menu_overrides.setdefault(item_id, {})
            row["price"] = payload.get("new_price")
            row["currency"] = payload.get("currency", "CAD")

    elif intent_type == "menu.item.recommendation_weight.set":
        item = payload.get("item_ref") or {}
        item_id = item.get("id")
        if item_id:
            weights = new_cfg.setdefault("recommendation_weights", {})
            weights[item_id] = payload.get("weight", "high")

    return new_cfg


def insert_change(
    conn,
    intent: Dict[str, Any],
    previous_state: Dict[str, Any],
    new_state: Dict[str, Any],
) -> str:
    change_id = f"chg_{uuid.uuid4().hex[:18]}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO config_changes (
                change_id, tenant_id, restaurant_id, intent_id, action_type, payload,
                previous_state, new_state, applied, applied_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP)
            """,
            (
                change_id,
                intent["tenant_id"],
                intent["restaurant_id"],
                intent["intent_id"],
                intent["intent_type"],
                json.dumps(intent["payload"]),
                json.dumps(previous_state),
                json.dumps(new_state),
            ),
        )
    return change_id


def execute_order_query(conn, tenant_id: str, restaurant_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    filters = payload.get("filters", {})
    statuses = filters.get("status") or ["confirmed", "pending", "failed"]
    limit = int(payload.get("limit", 20))

    where_parts = ["restaurant_id = %s", "status = ANY(%s)"]
    params: List[Any] = [restaurant_id, statuses]

    if filters.get("from"):
        where_parts.append("created_at >= %s")
        params.append(filters["from"])
    if filters.get("to"):
        where_parts.append("created_at <= %s")
        params.append(filters["to"])

    sql = f"""
      SELECT id, customer_phone, total, status, created_at
      FROM orders
      WHERE {' AND '.join(where_parts)}
      ORDER BY created_at DESC
      LIMIT %s
    """
    params.append(limit)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

    aggregation = payload.get("aggregation", "list")
    if aggregation == "count":
        return {"count": len(rows), "items": []}
    if aggregation == "sum":
        return {"count": len(rows), "sum_total": float(sum(float(r["total"]) for r in rows)), "items": []}

    clean_rows = []
    for row in rows:
        clean_rows.append(
            {
                "id": row["id"],
                "customer_phone": row["customer_phone"],
                "total": float(row["total"]),
                "status": row["status"],
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            }
        )
    return {"count": len(clean_rows), "items": clean_rows}


def undo_change(
    conn,
    tenant_id: str,
    restaurant_id: int,
    actor_id: str,
    source: str,
    target_change_id: Optional[str] = None,
) -> Dict[str, Any]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if target_change_id:
            cur.execute(
                """
                SELECT * FROM config_changes
                WHERE change_id = %s AND tenant_id = %s AND restaurant_id = %s AND rolled_back = FALSE
                LIMIT 1
                """,
                (target_change_id, tenant_id, restaurant_id),
            )
        else:
            cur.execute(
                """
                SELECT * FROM config_changes
                WHERE tenant_id = %s AND restaurant_id = %s AND rolled_back = FALSE
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tenant_id, restaurant_id),
            )
        row = cur.fetchone()

    if not row:
        return {"status": "error", "message": "No reversible change found."}

    previous_state = row["previous_state"]
    persist_config_snapshot(conn, tenant_id, restaurant_id, previous_state)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE config_changes SET rolled_back = TRUE, rolled_back_at = CURRENT_TIMESTAMP WHERE change_id = %s",
            (row["change_id"],),
        )

    insert_audit_log(
        conn,
        tenant_id,
        restaurant_id,
        actor_id,
        source,
        "config.rollback",
        {"change_id": row["change_id"]},
    )
    return {"status": "success", "change_id": row["change_id"]}


def execute_nl_command(req: NLCommandRequest) -> Dict[str, Any]:
    intent = parse_nl_command(req)

    conn = get_db_connection()
    try:
        status = "parsed"
        insert_intent(conn, intent, status=status)

        if intent["validation_errors"]:
            update_intent_status(conn, intent["intent_id"], "clarification_needed")
            conn.commit()
            return {
                "intent_id": intent["intent_id"],
                "status": "clarification_needed",
                "human_summary": "需要澄清后才能执行。" if intent["language"] == "zh" else "Need clarification before execution.",
                "change_id": None,
                "undo_token": None,
                "errors": intent["validation_errors"],
            }

        if req.dry_run:
            update_intent_status(conn, intent["intent_id"], "dry_run")
            conn.commit()
            return {
                "intent_id": intent["intent_id"],
                "status": "dry_run",
                "human_summary": build_summary(intent["intent_type"], intent["payload"], intent["language"]),
                "change_id": None,
                "undo_token": None,
                "errors": [],
            }

        if intent["requires_confirmation"] and not req.confirm:
            update_intent_status(conn, intent["intent_id"], "needs_confirmation")
            conn.commit()
            return {
                "intent_id": intent["intent_id"],
                "status": "needs_confirmation",
                "human_summary": build_summary(intent["intent_type"], intent["payload"], intent["language"]),
                "change_id": None,
                "undo_token": None,
                "errors": [],
            }

        if intent["intent_type"] == "ops.undo":
            result = undo_change(
                conn,
                tenant_id=req.tenant_id,
                restaurant_id=req.restaurant_id,
                actor_id=req.actor_id,
                source=req.source,
            )
            final_status = "applied" if result["status"] == "success" else "rejected"
            update_intent_status(conn, intent["intent_id"], final_status)
            conn.commit()
            return {
                "intent_id": intent["intent_id"],
                "status": final_status,
                "human_summary": build_summary(intent["intent_type"], intent["payload"], intent["language"]),
                "change_id": result.get("change_id"),
                "undo_token": None,
                "errors": [] if result["status"] == "success" else [result["message"]],
            }

        if intent["intent_type"] == "order.query":
            query_result = execute_order_query(conn, req.tenant_id, req.restaurant_id, intent["payload"])
            update_intent_status(conn, intent["intent_id"], "applied")
            insert_audit_log(
                conn,
                req.tenant_id,
                req.restaurant_id,
                req.actor_id,
                req.source,
                "order.query",
                {"intent_id": intent["intent_id"], "result": query_result},
            )
            conn.commit()
            return {
                "intent_id": intent["intent_id"],
                "status": "applied",
                "human_summary": build_summary(intent["intent_type"], intent["payload"], intent["language"]),
                "change_id": None,
                "undo_token": None,
                "errors": [],
                "query_result": query_result,
            }

        current_cfg = get_runtime_config(conn, req.tenant_id, req.restaurant_id)
        next_cfg = apply_intent_to_config(current_cfg, intent)

        change_id = insert_change(conn, intent, current_cfg, next_cfg)
        persist_config_snapshot(conn, req.tenant_id, req.restaurant_id, next_cfg)
        update_intent_status(conn, intent["intent_id"], "applied")
        insert_audit_log(
            conn,
            req.tenant_id,
            req.restaurant_id,
            req.actor_id,
            req.source,
            "config.applied",
            {"intent_id": intent["intent_id"], "change_id": change_id, "intent_type": intent["intent_type"]},
        )

        conn.commit()
        return {
            "intent_id": intent["intent_id"],
            "status": "applied",
            "human_summary": build_summary(intent["intent_type"], intent["payload"], intent["language"]),
            "change_id": change_id,
            "undo_token": f"undo_{change_id}",
            "errors": [],
        }
    except Exception as exc:
        conn.rollback()
        logger.error("execute_nl_command error: %s", exc)
        return {
            "intent_id": intent.get("intent_id", "unknown"),
            "status": "rejected",
            "human_summary": "执行失败。" if intent.get("language") == "zh" else "Execution failed.",
            "change_id": None,
            "undo_token": None,
            "errors": [str(exc)],
        }
    finally:
        conn.close()


# --- API Routes ---
@app.on_event("startup")
def startup() -> None:
    ensure_saas_tables()
    load_data_from_db()


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    return {"status": "ok", "db_connected": len(ITEMS_DB) > 0}


@app.post("/nl/command")
async def nl_command(req: NLCommandRequest) -> Dict[str, Any]:
    return execute_nl_command(req)


@app.post("/nl/confirm")
async def nl_confirm(req: NLConfirmRequest) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM nl_intents WHERE intent_id = %s", (req.intent_id,))
            row = cur.fetchone()

        if not row:
            return {"status": "error", "message": "Intent not found."}

        cmd = NLCommandRequest(
            text=row["raw_text"],
            tenant_id=row["tenant_id"],
            restaurant_id=row["restaurant_id"],
            actor_id=req.actor_id,
            source=row["source"] or "chat",
            language=row["language"],
            confirm=True,
            dry_run=False,
        )
        return execute_nl_command(cmd)
    finally:
        conn.close()


@app.post("/nl/undo")
async def nl_undo(req: NLUndoRequest) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        result = undo_change(conn, req.tenant_id, req.restaurant_id, req.actor_id, req.source, req.change_id)
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


@app.get("/nl/config")
async def get_nl_config(tenant_id: str = DEFAULT_TENANT_ID, restaurant_id: int = CURRENT_RESTAURANT_ID) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        cfg = get_runtime_config(conn, tenant_id, restaurant_id)
        return {"tenant_id": tenant_id, "restaurant_id": restaurant_id, "config": cfg}
    finally:
        conn.close()


@app.post("/webhook")
async def vapi_webhook(request: Request) -> Dict[str, Any]:
    raw_body = await request.body()
    verify_webhook_signature(raw_body, request.headers.get("x-vapi-signature"))

    call_id = request.headers.get("x-vapi-call-id")
    body = json.loads(raw_body.decode("utf-8") or "{}")
    message = body.get("message", {})
    message_type = message.get("type")
    tenant_id, restaurant_id = resolve_webhook_context(request, message)
    enforce_webhook_rate_limit(call_id or "unknown")

    customer_phone = "Unknown"
    if message_type == "tool-calls":
        call_obj = message.get("call", {})
        if not call_id:
            call_id = call_obj.get("id", "unknown_call")
        customer_phone = call_obj.get("customer", {}).get("number", "Unknown")

    if message_type == "tool-calls":
        session = get_session(call_id, tenant_id, restaurant_id)
        restaurant_items_db, restaurant_search_index = get_menu_cache(restaurant_id)
        tool_calls = message.get("toolCalls", [])
        results = []

        for tool in tool_calls:
            function_name = tool.get("function", {}).get("name")
            args = tool.get("function", {}).get("arguments", {})
            call_tool_id = tool.get("id")
            logger.info("[%s] Executing %s args=%s", call_id, function_name, args)
            result_content = ""
            message_id = request.headers.get("x-vapi-message-id") or message.get("id") or "unknown_message"
            idempotency_key = f"{tenant_id}:{restaurant_id}:{message_id}:{call_tool_id}"

            try:
                conn_idem = get_db_connection()
                try:
                    cached = get_stored_webhook_response(conn_idem, idempotency_key)
                finally:
                    conn_idem.close()

                if cached is not None:
                    results.append({"toolCallId": call_tool_id, "result": cached})
                    continue

                if function_name == "search_menu":
                    query = args.get("query", "")
                    lang = args.get("lang", "en")
                    session["lang"] = lang

                    matches = process.extract(
                        query.lower(),
                        [x[0] for x in restaurant_search_index],
                        scorer=fuzz.partial_ratio,
                        limit=3,
                        score_cutoff=40,
                    )

                    found_items = []
                    for match in matches:
                        idx = match[2]
                        item_id = restaurant_search_index[idx][1]
                        item = restaurant_items_db[item_id]
                        found_items.append(
                            {
                                "id": item["id"],
                                "name": item["name_zh"] if lang == "zh" else item["name_en"],
                                "price": item["price"],
                                "score": match[1],
                            }
                        )

                    if not found_items:
                        result_content = json.dumps({"status": "no_match", "message": "No items found."})
                    else:
                        result_content = json.dumps({"status": "success", "matches": found_items}, ensure_ascii=False)

                elif function_name == "add_item":
                    item_id = args.get("item_id")
                    qty = int(args.get("qty", 1))
                    notes = args.get("notes", "")

                    if item_id in restaurant_items_db:
                        item = restaurant_items_db[item_id]
                        cart_item = {
                            "id": item["id"],
                            "name_en": item["name_en"],
                            "name_zh": item["name_zh"],
                            "price": item["price"],
                            "qty": qty,
                            "notes": notes,
                        }
                        session["cart"].append(cart_item)
                        _, _, total = calculate_totals(session["cart"])

                        msg = f"Added {qty}x {item['name_en']}."
                        if session["lang"] == "zh":
                            msg = f"已添加 {qty}份 {item['name_zh']}。"

                        result_content = json.dumps(
                            {
                                "status": "success",
                                "message": msg,
                                "cart_count": len(session["cart"]),
                                "current_total": format_price(total),
                            },
                            ensure_ascii=False,
                        )
                    else:
                        result_content = json.dumps({"status": "error", "message": "Item ID not found."})

                elif function_name == "get_order_summary":
                    lang = args.get("lang", "en")
                    cart = session["cart"]

                    if not cart:
                        result_content = "Your cart is empty." if lang == "en" else "您的购物车是空的。"
                    else:
                        _, _, total = calculate_totals(cart)
                        lines = []
                        if lang == "zh":
                            lines.append("您目前的订单包括：")
                            for item in cart:
                                note_str = f" ({item['notes']})" if item.get("notes") else ""
                                lines.append(f"{item['qty']}份 {item['name_zh']}{note_str}")
                            lines.append(f"总计: {format_price(total)} (含税)")
                        else:
                            lines.append("You have ordered:")
                            for item in cart:
                                note_str = f" ({item['notes']})" if item.get("notes") else ""
                                lines.append(f"{item['qty']}x {item['name_en']}{note_str}")
                            lines.append(f"Total: {format_price(total)} (with tax)")
                        result_content = "\n".join(lines)

                elif function_name == "submit_order":
                    cart = session["cart"]
                    if not cart:
                        result_content = "Cart is empty."
                    else:
                        order_id = f"ORD-{uuid.uuid4().hex[:10].upper()}"
                        _, _, total = calculate_totals(cart)
                        inserted = save_order_to_db(
                            order_id=order_id,
                            restaurant_id=restaurant_id,
                            cart=cart,
                            total=total,
                            phone=customer_phone,
                            source_event_id=idempotency_key,
                        )

                        items_text = "\n".join([f"{i['qty']}x {i['name_en']}" for i in cart])
                        sms_body = f"[New Order] #{order_id}\nTotal: {format_price(total)}\nItems:\n{items_text}"

                        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
                        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
                        twilio_from = os.getenv("TWILIO_PHONE_NUMBER")
                        store_phone = os.getenv("STORE_PHONE_NUMBER")

                        if twilio_sid:
                            try:
                                from twilio.rest import Client

                                client = Client(twilio_sid, twilio_token)
                                client.messages.create(body=sms_body, from_=twilio_from, to=store_phone)
                            except Exception as exc:
                                logger.error("SMS Error: %s", exc)

                        session["cart"] = []
                        result_content = json.dumps(
                            {
                                "status": "success" if inserted else "duplicate",
                                "order_id": order_id,
                                "message_en": f"Order {order_id} confirmed. SMS sent." if inserted else "Duplicate submit ignored.",
                                "message_zh": f"订单 {order_id} 已确认，厨房已收到短信。" if inserted else "重复提交已忽略。",
                            },
                            ensure_ascii=False,
                        )

                elif function_name == "transfer_to_human":
                    lang = args.get("lang", "en")
                    transfer_number = os.getenv("TRANSFER_PHONE_NUMBER", "+15550000000")
                    result_content = (
                        f"Transferring you to {transfer_number}."
                        if lang == "en"
                        else f"正在为您转接 {transfer_number}。"
                    )

                elif function_name == "execute_nl_command":
                    nl_req = NLCommandRequest(
                        text=args.get("text", ""),
                        tenant_id=args.get("tenant_id", tenant_id),
                        restaurant_id=int(args.get("restaurant_id", restaurant_id)),
                        actor_id=args.get("actor_id", "owner"),
                        source=args.get("source", "chat"),
                        language=args.get("language"),
                        confirm=bool(args.get("confirm", False)),
                        dry_run=bool(args.get("dry_run", False)),
                    )
                    result_content = json.dumps(execute_nl_command(nl_req), ensure_ascii=False)

                elif function_name == "undo_last_config_change":
                    conn = get_db_connection()
                    try:
                        undo_result = undo_change(
                            conn,
                            tenant_id=args.get("tenant_id", tenant_id),
                            restaurant_id=int(args.get("restaurant_id", restaurant_id)),
                            actor_id=args.get("actor_id", "owner"),
                            source=args.get("source", "chat"),
                            target_change_id=args.get("change_id"),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    result_content = json.dumps(undo_result, ensure_ascii=False)

                elif function_name == "query_orders":
                    conn = get_db_connection()
                    try:
                        query_payload = {
                            "filters": args.get("filters", {}),
                            "aggregation": args.get("aggregation", "list"),
                            "limit": int(args.get("limit", 20)),
                        }
                        query_result = execute_order_query(
                            conn,
                            tenant_id=args.get("tenant_id", tenant_id),
                            restaurant_id=int(args.get("restaurant_id", restaurant_id)),
                            payload=query_payload,
                        )
                    finally:
                        conn.close()
                    result_content = json.dumps({"status": "success", "result": query_result}, ensure_ascii=False)

                else:
                    result_content = f"Tool {function_name} not implemented."

            except Exception as exc:
                logger.error("Error executing %s: %s", function_name, exc)
                result_content = f"Error: {str(exc)}"

            save_session(call_id, tenant_id, restaurant_id, session)
            conn_idem = get_db_connection()
            try:
                store_webhook_response(conn_idem, idempotency_key, call_id, function_name, result_content)
                conn_idem.commit()
            finally:
                conn_idem.close()
            results.append({"toolCallId": call_tool_id, "result": result_content})

        return {"results": results}

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
