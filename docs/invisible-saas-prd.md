# Invisible SaaS PRD (Lite)

## 1. Product Positioning
- Product name (working): AI Store Operator
- Core value: restaurant owners configure and operate by natural language, not by dashboards.
- Principle: exception-driven operations; no daily learning cost.

## 2. Target Users
- Primary: single-store or multi-store restaurant owners/managers.
- Secondary: shift supervisors who handle urgent operations.

## 3. Non-Goals (v1)
- No complex RBAC UI.
- No BI-heavy dashboard as default homepage.
- No manual workflow builder.

## 4. Success Metrics (v1)
- Time to first value (TTFV): <= 10 minutes from signup to first successful AI-managed order.
- Natural-language config success rate: >= 90% (intent parsed and executed without manual correction).
- Weekly active operators using NL commands: >= 60%.
- Exception handling SLA: critical exception notified within 30 seconds.

## 5. Core Experience
- Default entrypoint is a conversation inbox (chat/voice), not a dashboard.
- Owner sends commands like:
  - "Tonight after 10pm transfer all calls to this number..."
  - "Pause lobster congee for today."
  - "If customer asks for a human, transfer immediately."
- System replies with:
  - Parsed intent summary
  - Effective time window
  - Risk warning if any
  - Confirm / Cancel / Undo options

## 6. Core Features (v1)
1. AI Operator Command Center
- Natural language command parsing and execution.
- Confirmation policy:
  - Low-risk config: auto-apply with undo token.
  - High-risk config (hours/routing/menu price): require explicit confirmation.
- One-click rollback for last applied command.

2. Exception Center
- Notify only abnormal events:
  - order submit failed
  - handoff failed
  - SMS failed
  - low-confidence menu parse
  - abnormal drop in order conversion
- Each exception includes "Fix Suggestion + One-click Action".

3. Order Query by Natural Language
- Examples:
  - "Show me unconfirmed orders since 2pm."
  - "How many transferred calls today?"
- Return concise summary first, details on demand.

4. Photo Menu Upload (Killer Feature)
- Upload image/PDF -> OCR + structure extraction -> draft menu.
- Highlight uncertain fields only (price/item name/category).
- "Confirm and publish" flow with safe checks (duplicates/outliers).

## 7. User Journey (v1)
1. Onboarding
- Connect phone number + basic store info.
- Upload menu photos or existing menu file.
- Assistant runs simulation call and confirms readiness.

2. Daily operation
- No dashboard needed.
- Receive only exceptions and key summaries.
- Configure behavior through conversation.

3. Escalation
- On exception, operator receives actionable card with one-click fix.

## 8. Functional Requirements
- Command parser supports:
  - schedule rules
  - transfer routing
  - menu availability
  - recommendation strategy
  - order query
- Execution engine supports:
  - dry-run explanation
  - idempotent apply
  - versioned config changes
  - rollback
- Audit log:
  - who/what/when/source message/executed action/result

## 9. System Requirements
- Multi-tenant hard isolation (`tenant_id` on all business tables).
- Webhook security: signature verification + replay prevention.
- Config change ledger with versioning.
- Redis-backed session/state (no in-memory only).
- Alerting pipeline for critical failures.

## 10. Risks and Mitigation
- Risk: NL ambiguity causes wrong config.
  - Mitigation: confidence thresholds + explicit confirmation + undo.
- Risk: owner trust loss due to silent changes.
  - Mitigation: every change has summary and audit trail.
- Risk: menu OCR quality variance.
  - Mitigation: uncertainty highlighting and partial confirmation UX.

## 11. v1 Scope Boundaries
- Include:
  - command center
  - exception center
  - NL order query
  - menu photo upload with human confirmation
- Exclude:
  - advanced analytics suite
  - granular role matrix UI
  - custom workflow builder

