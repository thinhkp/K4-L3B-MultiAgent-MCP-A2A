"""Evidence scoped L3B investigation with explicit specialist handoffs."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _ids(values: Any) -> list[str]:
    source = values if isinstance(values, list) else [values]
    return list(dict.fromkeys(value for value in source if isinstance(value, str) and value))[:20]


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _number(value: Decimal | None) -> float | None:
    return float(value.quantize(Decimal("0.01"))) if value is not None else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _current(
    rows: list[dict[str, Any]],
    time_key: str,
    purchase: datetime | None,
    *,
    days: int = 5,
) -> list[dict[str, Any]]:
    if purchase is None:
        return rows
    return [
        row
        for row in rows
        if (when := _time(row.get(time_key))) is not None
        and purchase - timedelta(days=1) <= when <= purchase + timedelta(days=days)
    ]


class Investigation:
    def __init__(self, case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter):
        self.case, self.gateway, self.trace = case, gateway, trace
        self.case_id: str = case["case_id"]
        self.available: set[str] = set()
        self.cache: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = {}
        self.refs: dict[str, str] = {}

    def assign(self, actor: str) -> None:
        self.trace.emit(
            case_id=self.case_id, event_type="task_assigned", actor="coordinator", target=actor
        )

    def handoff(self, actor: str, target: str = "coordinator", code: str | None = None) -> None:
        self.trace.emit(
            case_id=self.case_id,
            event_type="handoff",
            actor=actor,
            target=target,
            decision_code=code,
        )

    async def get(self, actor: str, tool: str, **arguments: str) -> Any:
        if tool not in self.available or not all(arguments.values()):
            return None
        key = (tool, tuple(sorted(arguments.items())))
        if key in self.cache:
            return self.cache[key]
        try:
            envelope = await self.gateway.call(tool, case_id=self.case_id, **arguments)
        except (RuntimeError, ValueError, OSError):
            return None
        self.cache[key] = envelope["data"]
        self.refs[tool] = envelope["evidence_ref"]
        self.trace.emit(
            case_id=self.case_id,
            event_type="tool_result_consumed",
            actor=actor,
            tool_name=tool,
            evidence_refs=[envelope["evidence_ref"]],
        )
        return envelope["data"]

    def references(self, *tools: str) -> list[str]:
        return list(dict.fromkeys(self.refs[tool] for tool in tools if tool in self.refs))[:30]


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    investigation = Investigation(case, gateway, trace)
    investigation.available = set(await gateway.list_tools())
    request = _dict(case.get("customer_request"))
    opened = _time(case.get("opened_at"))
    claims = _list(request.get("claims"))
    topics = _ids([claim.get("topic") for claim in claims])
    claimed_id = request.get("claimed_order_id")
    candidates = _ids([claimed_id, *_ids(case.get("candidate_order_ids"))])[:5]

    investigation.assign("entity-agent")
    orders: dict[str, dict[str, Any]] = {}
    if isinstance(claimed_id, str):
        response = _dict(await investigation.get("entity-agent", "get_order", order_id=claimed_id))
        if response.get("order_id") == claimed_id:
            orders[claimed_id] = response

    investigation.assign("customer-agent")
    hint = case.get("customer_unique_id_hint")
    customer = (
        _dict(
            await investigation.get(
                "customer-agent", "get_customer_history", customer_unique_id=hint
            )
        )
        if isinstance(hint, str)
        else {}
    )
    customer_id = (
        customer.get("customer_unique_id") if customer.get("customer_unique_id") == hint else None
    )
    history = _list(customer.get("orders"))
    related_orders = _ids([row.get("order_id") for row in history])
    investigation.handoff("customer-agent")

    # A single order ID can appear in several historical snapshots. Prefer the
    # order whose delivery deadline most recently passed when the claim opened.
    eligible = [
        row
        for row in history
        if row.get("order_id") in candidates
        and (when := _time(row.get("order_purchase_timestamp"))) is not None
        and (opened is None or when <= opened)
    ]
    due = [
        row
        for row in eligible
        if opened is not None
        and (deadline := _time(row.get("order_estimated_delivery_date"))) is not None
        and deadline <= opened
    ]
    selected = (
        max(due, key=lambda row: _time(row["order_estimated_delivery_date"]))
        if due
        else max(eligible, key=lambda row: _time(row["order_purchase_timestamp"]))
        if eligible
        else {}
    )
    if not selected and claimed_id in orders:
        direct = orders[claimed_id]
        direct_purchase = _time(direct.get("order_purchase_timestamp"))
        if opened is None or (direct_purchase is not None and direct_purchase <= opened):
            selected = direct
    if not selected and not history:
        for candidate in candidates:
            if candidate == claimed_id:
                continue
            response = _dict(
                await investigation.get("entity-agent", "get_order", order_id=candidate)
            )
            when = _time(response.get("order_purchase_timestamp"))
            if response.get("order_id") == candidate and (
                opened is None or when and when <= opened
            ):
                orders[candidate] = response
        if len(orders) == 1:
            selected = next(iter(orders.values()))
    order_id = selected.get("order_id")
    resolved = _ids(order_id)
    rejected = [candidate for candidate in candidates if candidate != order_id]
    entity_status = "resolved" if order_id else "ambiguous" if candidates else "not_found"
    investigation.handoff("entity-agent", code=entity_status)
    order = selected
    purchase = _time(order.get("order_purchase_timestamp"))

    investigation.assign("order-agent")
    items = (
        _list(await investigation.get("order-agent", "get_order_items", order_id=order_id))
        if order_id
        else []
    )
    current_items = _current(items, "shipping_limit_date", purchase)
    # MCP can include duplicate source rows for the same order item. The item
    # identifier is the unit of sale; duplicate copies must not double its price.
    current_items = list(
        {
            item.get("order_item_id", index): item for index, item in enumerate(current_items)
        }.values()
    )
    item_totals = [
        (_decimal(item.get("price")), _decimal(item.get("freight_value"))) for item in current_items
    ]
    expected = (
        sum((price + freight for price, freight in item_totals), Decimal(0))
        if current_items
        and all(price is not None and freight is not None for price, freight in item_totals)
        else None
    )
    investigation.handoff("order-agent")

    investigation.assign("payment-agent")
    payment_timeline = (
        _dict(await investigation.get("payment-agent", "get_payment_timeline", order_id=order_id))
        if order_id
        else {}
    )
    payment_rows = _list(payment_timeline.get("payments"))
    payment_events = _current(_list(payment_timeline.get("events")), "event_at", purchase, days=2)
    captures = [
        event
        for event in payment_events
        if event.get("event_type") == "captured" and event.get("status") == "confirmed"
    ]
    captured_values = [_decimal(event.get("amount_brl")) for event in captures]
    captured = (
        sum(captured_values, Decimal(0))
        if captures and all(v is not None for v in captured_values)
        else None
    )
    split_confirmed = False
    if "valid_split_payment" in topics and expected is not None:
        event_amounts = Counter(value for value in captured_values if value is not None)
        for size in range(2, min(len(payment_rows), 5) + 1):
            for group in combinations(payment_rows, size):
                amounts = [_decimal(row.get("payment_value")) for row in group]
                if any(amount is None for amount in amounts) or sum(amounts) != expected:
                    continue
                sequences = [row.get("payment_sequential") for row in group]
                kinds = {row.get("payment_type") for row in group}
                if len(set(sequences)) != size or len(kinds) < 2:
                    continue
                if all(
                    event_amounts[amount] >= count for amount, count in Counter(amounts).items()
                ):
                    captured = expected
                    split_confirmed = True
                    break
            if split_confirmed:
                break
    refund_timeline = (
        _dict(await investigation.get("payment-agent", "get_refund_timeline", order_id=order_id))
        if order_id and any(topic.startswith("refund_") for topic in topics)
        else {}
    )
    refund_events = _current(_list(refund_timeline.get("events")), "event_at", purchase, days=120)
    if opened is not None:
        refund_events = [
            event
            for event in refund_events
            if (when := _time(event.get("event_at"))) is not None
            and when <= opened + timedelta(days=14)
        ]
    completed_refunds = [
        _decimal(event.get("amount_brl"))
        for event in refund_events
        if event.get("status") in {"completed", "refunded", "succeeded"}
    ]
    refunded = (
        sum(completed_refunds, Decimal(0))
        if completed_refunds and all(v is not None for v in completed_refunds)
        else Decimal(0)
        if refund_timeline
        else None
    )
    refundable = (
        max(Decimal(0), captured - refunded)
        if captured is not None and refunded is not None
        else None
    )
    refund_state = next(
        (
            event.get("status")
            for event in reversed(refund_events)
            if event.get("status") in {"pending", "failed", "completed", "refunded"}
        ),
        None,
    )
    if refund_state == "failed":
        payment_verdict = "refund_failed"
    elif refund_state == "pending":
        payment_verdict = "refund_pending"
    elif refund_state in {"completed", "refunded"}:
        payment_verdict = "refunded"
    elif split_confirmed:
        payment_verdict = "reconciled"
    elif captured is None or expected is None:
        payment_verdict = "insufficient_evidence"
    elif captured > expected and len(captures) > 1:
        payment_verdict = "duplicate_capture"
    elif captured != expected:
        payment_verdict = "capture_mismatch"
    else:
        payment_verdict = "reconciled"
    investigation.handoff("payment-agent")

    investigation.assign("shipment-agent")
    shipment = (
        _dict(await investigation.get("shipment-agent", "get_shipment_summary", order_id=order_id))
        if order_id
        else {}
    )
    carrier = _time(order.get("order_delivered_carrier_date"))
    delivered = _time(order.get("order_delivered_customer_date"))
    estimated = _time(order.get("order_estimated_delivery_date"))
    current_limits = _current(_list(shipment.get("shipping_limits")), "shipping_limit_at", purchase)
    seller_late = bool(
        carrier
        and current_limits
        and any(
            (limit := _time(row.get("shipping_limit_at"))) and carrier > limit
            for row in current_limits
        )
    )
    if order.get("order_status") == "unavailable":
        shipment_verdict = "lost"
    elif delivered and estimated:
        shipment_verdict = (
            "seller_delay"
            if seller_late
            else "logistics_delay"
            if delivered > estimated
            else "on_time"
        )
    else:
        shipment_verdict = "insufficient_evidence"
    late_sellers = _ids(
        [
            row.get("seller_id")
            for row in current_limits
            if carrier and (limit := _time(row.get("shipping_limit_at"))) and carrier > limit
        ]
    )
    investigation.handoff("shipment-agent")

    investigation.assign("policy-agent")
    policy = _dict(
        await investigation.get(
            "policy-agent", "get_policy", policy_version=case.get("policy_version")
        )
    )
    rules = _dict(policy.get("rules"))
    # Observed current records take precedence over the claim and historical rows.
    order_status = order.get("order_status")
    if not order_id or not policy:
        issue = "insufficient_evidence"
    elif order_status == "canceled" and captured is not None and captured > 0:
        issue = "canceled_order_paid"
    elif order_status == "unavailable" and captured is not None and captured > 0:
        issue = "unavailable_order_paid"
    elif shipment_verdict == "seller_delay":
        issue = "late_delivery_seller"
    elif shipment_verdict == "logistics_delay":
        issue = "late_delivery_logistics"
    elif "refund_failed" in topics and refund_state == "failed":
        issue = "refund_failed"
    elif "refund_pending" in topics and refund_state == "pending":
        issue = "refund_pending"
    elif payment_verdict == "duplicate_capture":
        issue = "duplicate_charge"
    elif payment_verdict == "capture_mismatch":
        issue = "payment_mismatch"
    elif payment_verdict == "reconciled" and (split_confirmed or len(captures) > 1):
        issue = "valid_split_payment"
    elif captured is not None and expected is not None and shipment_verdict == "on_time":
        issue = "unsupported_claim"
    else:
        issue = "insufficient_evidence"
    rule = _dict(rules.get(issue))
    refund_amount = _decimal(rule.get("refund_brl")) or Decimal(0)
    if refundable is not None:
        refund_amount = min(refund_amount, refundable)
    if issue == "insufficient_evidence":
        refund_amount = Decimal(0)
    case_status = rule.get("case_status", "needs_investigation")
    parties = _list(rule.get("responsible_parties"))[:5]
    action = rule.get("recommended_action")
    actions = [action] if isinstance(action, str) and action else []
    investigation.trace.emit(
        case_id=investigation.case_id,
        event_type="policy_decided",
        actor="policy-agent",
        decision_code=issue,
    )
    investigation.handoff("policy-agent", target="verifier-agent")

    # Multiple snapshots sharing an ID must be resolved by complaint time.
    history_conflict = bool(
        purchase
        and (
            any(
                row.get("order_id") == order_id
                and _time(row.get("order_purchase_timestamp")) != purchase
                for row in history
            )
            or (
                order_id in orders
                and _time(orders[order_id].get("order_purchase_timestamp")) != purchase
            )
        )
    )
    conflicts = (
        [
            {
                "field": "order_purchase_timestamp",
                "sources": ["get_order", "get_customer_history"],
                "selected_source": "get_customer_history"
                if selected is not orders.get(order_id)
                else "get_order",
                "resolution_code": "COMPLAINT_TIME_PRECEDENCE",
            }
        ]
        if history_conflict
        else []
    )
    relevant_tools = ["get_order", "get_customer_history", "get_policy"]
    if issue in {"late_delivery_seller", "late_delivery_logistics", "unsupported_claim"}:
        relevant_tools.extend(["get_shipment_summary", "get_order_items"])
    if issue in {
        "canceled_order_paid",
        "unavailable_order_paid",
        "payment_mismatch",
        "duplicate_charge",
        "valid_split_payment",
        "refund_pending",
        "refund_failed",
        "unsupported_claim",
    }:
        relevant_tools.extend(["get_payment_timeline", "get_order_items"])
    if issue in {"refund_pending", "refund_failed"}:
        relevant_tools.append("get_refund_timeline")
    claim_refs = investigation.references(*relevant_tools)
    claim_assessments = []
    for claim in claims[:5]:
        if not isinstance(claim.get("claim_id"), str):
            continue
        topic = claim.get("topic")
        if issue == "insufficient_evidence":
            verdict = "insufficient_evidence"
        elif topic == issue:
            verdict = "supported"
        elif topic == "requested_full_refund" and refund_amount > 0:
            verdict = (
                "supported"
                if captured is not None and refund_amount >= captured
                else "partially_supported"
            )
        else:
            verdict = "unsupported"
        claim_assessments.append(
            {
                "claim_id": claim["claim_id"],
                "verdict": verdict,
                "confidence": 0.82 if issue != "insufficient_evidence" else 0.3,
                "evidence_refs": claim_refs,
            }
        )
    output: dict[str, Any] = {
        "schema_version": "day09-l3b-output-v2",
        "case_id": investigation.case_id,
        "assessment": {
            "primary_issue": issue,
            "secondary_issues": [],
            "case_status": case_status,
            "confidence": 0.82 if issue != "insufficient_evidence" else 0.25,
        },
        "affected_entities": {
            "order_ids": _ids(order_id),
            "item_ids": _ids([row.get("order_item_id") for row in current_items]),
            "seller_ids": _ids([row.get("seller_id") for row in current_items]),
            "payment_references": _ids([row.get("payment_reference") for row in payment_rows]),
            "shipment_ids": _ids(shipment.get("shipment_id")),
        },
        "entity_resolution": {
            "status": entity_status,
            "resolved_order_ids": _ids(resolved),
            "rejected_candidates": _ids(rejected),
            "confidence": 0.9 if order_id else 0.2,
        },
        "customer_context": {
            "customer_unique_id": customer_id,
            "related_order_ids": related_orders,
        },
        "shipment_analysis": {
            "verdict": shipment_verdict,
            "late_seller_ids": late_sellers,
            "timeline_complete": bool(carrier and delivered and estimated),
        },
        "payment_analysis": {
            "verdict": payment_verdict,
            "captured_total_brl": _number(captured),
            "refunded_total_brl": _number(refunded),
            "refundable_total_brl": _number(refundable),
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": issue.upper(), "rank": 1}]
            if issue != "insufficient_evidence"
            else [],
            "responsible_parties": parties,
        },
        "evidence_refs": claim_refs,
        "data_conflicts": conflicts,
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": _number(refund_amount),
            "refund_lines": [
                {"reason_code": issue, "amount_brl": _number(refund_amount), "entity_id": order_id}
            ]
            if refund_amount > 0
            else [],
        },
        "resolution_actions": actions,
    }
    if claim_assessments:
        output["claim_assessments"] = claim_assessments
    investigation.assign("verifier-agent")
    if issue != "insufficient_evidence" and not claim_refs:
        raise ValueError("conclusion has no MCP evidence")
    if refund_amount > 0 and refundable is not None and refund_amount > refundable:
        raise ValueError("refund exceeds remaining captured amount")
    investigation.trace.emit(
        case_id=investigation.case_id,
        event_type="verification_completed",
        actor="verifier-agent",
        decision_code="verified",
    )
    return output
