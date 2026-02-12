# Natural Language Command Mapping (Top 20)

## Format
- Input: example operator phrase.
- Parsed intent: canonical action type.
- Key payload: minimum fields required.
- Confirmation: `yes/no`.

## Commands
1. Input: "今晚10点后把电话转到 +1 437-299-9468"
- Parsed intent: `routing.transfer_rule.upsert`
- Key payload: `trigger=after_hours`, `phone_number=+14372999468`, `start_at=today 22:00 local`
- Confirmation: `yes`

2. Input: "如果客人要求人工，马上转接到这个号码 +1 437-299-9468"
- Parsed intent: `routing.handoff_policy.set`
- Key payload: `trigger=user_requests_human`, `target_number=+14372999468`, `immediate=true`
- Confirmation: `yes`

3. Input: "周一到周五营业时间改成 11点到22点"
- Parsed intent: `hours.business_hours.set`
- Key payload: `days=mon..fri`, `open=11:00`, `close=22:00`
- Confirmation: `yes`

4. Input: "明天暂停龙虾粥"
- Parsed intent: `menu.item.availability.set`
- Key payload: `item=congee_001`, `available=false`, `effective_until=tomorrow 23:59`
- Confirmation: `yes`

5. Input: "把扬州炒饭价格改成 18.5"
- Parsed intent: `menu.item.price.set`
- Key payload: `item=rice_003`, `new_price=18.5`, `currency=CAD`
- Confirmation: `yes`

6. Input: "今天海鲜类都先下架"
- Parsed intent: `menu.category.availability.set`
- Key payload: `category=seafood`, `available=false`, `effective_until=today 23:59`
- Confirmation: `yes`

7. Input: "恢复龙虾粥上架"
- Parsed intent: `menu.item.availability.set`
- Key payload: `item=congee_001`, `available=true`
- Confirmation: `no`

8. Input: "以后晚上推荐先说炒饭，再推荐粥"
- Parsed intent: `menu.item.recommendation_weight.set`
- Key payload: `time_window=18:00-23:00`, `rank=[fried_rice, congee]`
- Confirmation: `no`

9. Input: "今天下午2点以后有多少单没确认？"
- Parsed intent: `order.query`
- Key payload: `status=pending`, `from=today 14:00`, `aggregation=count`
- Confirmation: `no`

10. Input: "给我看今天转人工的订单"
- Parsed intent: `order.query`
- Key payload: `has_transfer=true`, `from=today 00:00`, `to=now`, `aggregation=list`
- Confirmation: `no`

11. Input: "把所有英文来电转给 4372999468"
- Parsed intent: `routing.transfer_rule.upsert`
- Key payload: `language=en`, `phone_number=+14372999468`, `trigger=always`
- Confirmation: `yes`

12. Input: "忙线时先让 AI 继续接，不要转人工"
- Parsed intent: `routing.handoff_policy.set`
- Key payload: `busy_line_policy=ai_continue`, `human_handoff=false`
- Confirmation: `yes`

13. Input: "如果订单金额超过 100 刀再转人工确认"
- Parsed intent: `routing.transfer_rule.upsert`
- Key payload: `trigger=high_value_order`, `min_order_amount=100`, `currency=CAD`
- Confirmation: `yes`

14. Input: "明天中午 11 点到 1 点只接中文"
- Parsed intent: `language.policy.set`
- Key payload: `window=tomorrow 11:00-13:00`, `allowed_languages=["zh"]`
- Confirmation: `yes`

15. Input: "把昨天失败的订单发我摘要"
- Parsed intent: `order.query`
- Key payload: `status=failed`, `from=yesterday 00:00`, `to=yesterday 23:59`, `aggregation=list`
- Confirmation: `no`

16. Input: "现在开始，推荐里优先泰式炒河"
- Parsed intent: `menu.item.recommendation_weight.set`
- Key payload: `item=thai_002`, `weight=high`, `effective_at=immediate`
- Confirmation: `no`

17. Input: "撤回刚刚那条配置"
- Parsed intent: `ops.undo`
- Key payload: `target_change=last_change_id`
- Confirmation: `no`

18. Input: "取消所有转接规则"
- Parsed intent: `routing.transfer_rule.delete`
- Key payload: `scope=all_rules`
- Confirmation: `yes`

19. Input: "今天打烊后自动短信通知我总订单和漏单"
- Parsed intent: `reporting.digest.schedule.set`
- Key payload: `time=close_time+5m`, `channels=["sms"]`, `metrics=["total_orders","failed_orders"]`
- Confirmation: `no`

20. Input: "把这个新号码设为默认转接 +1 647-123-4567"
- Parsed intent: `routing.transfer_rule.upsert`
- Key payload: `trigger=default_handoff`, `phone_number=+16471234567`, `priority=highest`
- Confirmation: `yes`

## Clarification Templates
- Ambiguous menu item:
  - "我找到了 2 个可能的菜品：A、B。你要修改哪一个？"
- Ambiguous time:
  - "你说的'明晚'按本地时区是 2026-02-13 18:00 开始，确认吗？"
- Unsafe high-risk change:
  - "这是高风险改动（影响来电路由）。回复'确认执行'后我再生效。"

