# Natural Language Config DSL Spec (v1)

## 1. Purpose
- Convert operator natural-language commands into deterministic, executable actions.
- Ensure actions are safe, auditable, and reversible.

## 2. Pipeline
1. Input normalization
- Detect language (`en`/`zh`), normalize phone/time/date/menu aliases.

2. Intent parsing
- Produce structured `IntentEnvelope`.

3. Safety policy check
- Classify risk and decide auto-apply vs confirm.

4. Execution
- Write versioned config/event records and trigger runtime refresh.

5. Response
- Return human-readable summary + execution result + undo token.

## 3. Intent Envelope (canonical JSON)
```json
{
  "intent_id": "uuid",
  "tenant_id": "t_123",
  "restaurant_id": "r_123",
  "actor_id": "u_123",
  "source": "chat|voice|api",
  "language": "zh",
  "raw_text": "今晚10点后转到 +1 437-299-9468",
  "intent_type": "routing.transfer_rule.upsert",
  "confidence": 0.94,
  "requires_confirmation": true,
  "risk_level": "high",
  "effective_window": {
    "start_at": "2026-02-12T22:00:00-05:00",
    "end_at": null,
    "timezone": "America/Toronto"
  },
  "payload": {},
  "validation_errors": []
}
```

## 4. DSL Action Types
- `routing.transfer_rule.upsert`
- `routing.transfer_rule.delete`
- `routing.handoff_policy.set`
- `hours.business_hours.set`
- `menu.item.availability.set`
- `menu.item.price.set`
- `menu.item.recommendation_weight.set`
- `order.query`
- `ops.undo`

## 5. Payload Schemas

### 5.1 routing.transfer_rule.upsert
```json
{
  "rule_id": "optional",
  "trigger": "after_hours|user_requests_human|vip_customer|always",
  "phone_number": "+14372999468",
  "priority": 100,
  "conditions": {
    "language": "en|zh|any",
    "min_order_amount": null
  }
}
```

### 5.2 hours.business_hours.set
```json
{
  "days": ["mon", "tue", "wed", "thu", "fri"],
  "open_time": "11:00",
  "close_time": "22:00",
  "timezone": "America/Toronto"
}
```

### 5.3 menu.item.availability.set
```json
{
  "item_ref": {
    "id": "congee_001",
    "name": "Lobster Super Bowl Congee"
  },
  "available": false,
  "effective_until": "2026-02-13T00:00:00-05:00",
  "reason": "sold_out"
}
```

### 5.4 menu.item.price.set
```json
{
  "item_ref": {
    "id": "rice_003",
    "name": "Yeung Chow Fried Rice"
  },
  "new_price": 18.5,
  "currency": "CAD",
  "effective_at": "immediate"
}
```

### 5.5 order.query
```json
{
  "filters": {
    "status": ["pending", "confirmed", "failed"],
    "from": "2026-02-12T14:00:00-05:00",
    "to": "2026-02-12T23:59:59-05:00",
    "has_transfer": null
  },
  "aggregation": "count|sum|list",
  "limit": 20
}
```

### 5.6 ops.undo
```json
{
  "target_change_id": "chg_abc123",
  "reason": "operator_requested"
}
```

## 6. Safety Policy
- `low` risk: recommendation weight changes, read-only queries.
- `medium` risk: temporary item availability changes.
- `high` risk: routing, business hours, price changes.

Execution rule:
- `low`: auto-apply + notify.
- `medium`: auto-apply if confidence >= 0.9, else confirm.
- `high`: always require explicit confirmation.

## 7. Ambiguity Handling
- If confidence < 0.75: ask a clarification question, do not execute.
- If entity ambiguity (multiple menu matches): return top candidates and request selection.
- If temporal ambiguity ("tomorrow night"): resolve to absolute datetime in response before execution.

## 8. Response Contract
```json
{
  "intent_id": "uuid",
  "status": "needs_confirmation|applied|rejected|clarification_needed",
  "human_summary": "从今晚 10:00 开始，来电将转接到 +1 437-299-9468。",
  "change_id": "chg_abc123",
  "undo_token": "undo_abc123",
  "errors": []
}
```

## 9. Storage Model (minimum)
- `nl_intents`
- `config_changes`
- `config_snapshots`
- `audit_logs`

Every applied command must generate:
1. intent record
2. config change record
3. audit log

## 10. Versioning
- DSL version field: `dsl_version = "1.0"`.
- Backward compatible additions only in minor versions.
- Breaking changes require parser dual-read period.

