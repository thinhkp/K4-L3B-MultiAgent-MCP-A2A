from __future__ import annotations

import asyncio
from pathlib import Path

from student_agent.contracts import Contracts
from student_agent.workflow import solve_case


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def list_tools(self) -> list[str]:
        return [
            "get_order",
            "get_customer_history",
            "get_order_items",
            "get_order_payments",
            "get_payment_timeline",
            "get_shipment_summary",
            "get_policy",
        ]

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict:
        self.calls.append((case_id, tool_name))
        if arguments.get("order_id") == "bad-order":
            raise RuntimeError("unknown order")
        data = {
            "get_order": {
                "order_id": "real-order",
                "order_status": "canceled",
                "order_purchase_timestamp": "2018-01-01T09:00:00-03:00",
            },
            "get_customer_history": {"customer_unique_id": "customer-1", "orders": []},
            "get_order_items": [
                {
                    "order_item_id": "item-1",
                    "seller_id": "seller-1",
                    "price": "75.00",
                    "freight_value": "5.00",
                    "shipping_limit_date": "2018-01-03T09:00:00-03:00",
                }
            ],
            "get_order_payments": [],
            "get_payment_timeline": {
                "events": [
                    {
                        "event_type": "captured",
                        "status": "confirmed",
                        "amount_brl": "80.00",
                        "event_at": "2018-01-01T10:00:00-03:00",
                    }
                ]
            },
            "get_shipment_summary": {"order_status": "canceled"},
            "get_policy": {
                "rules": {
                    "canceled_order_paid": {
                        "case_status": "action_required",
                        "refund_brl": 80,
                        "recommended_action": "issue_refund",
                        "responsible_parties": [{"party_type": "platform", "party_id": None}],
                    }
                }
            },
        }[tool_name]
        return {"evidence_ref": "ev_" + tool_name.ljust(24, "x"), "data": data}


class FakeTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, **event: object) -> None:
        self.events.append(event)


def test_workflow_uses_current_case_evidence_and_valid_output() -> None:
    case = {
        "case_id": "L3B_CASE_TEST",
        "customer_request": {
            "claimed_order_id": "real-order",
            "claims": [{"claim_id": "claim-1", "topic": "canceled_order_paid"}],
        },
        "candidate_order_ids": ["real-order", "bad-order"],
        "customer_unique_id_hint": "customer-1",
        "policy_version": "EC_POLICY_V2",
    }
    gateway, trace = FakeGateway(), FakeTrace()
    output = asyncio.run(solve_case(case, gateway, trace))
    contracts = Contracts(Path(__file__).resolve().parents[1] / "contracts" / "schemas")
    contracts.validate_output(output, "test output")
    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["entity_resolution"]["rejected_candidates"] == ["bad-order"]
    assert output["financial_resolution"]["recommended_refund_brl"] == 80
    consumed = [event for event in trace.events if event["event_type"] == "tool_result_consumed"]
    assert set(output["evidence_refs"]).issubset(
        {ref for event in consumed for ref in event["evidence_refs"]}
    )
    assert all(case_id == case["case_id"] for case_id, _ in gateway.calls)
    assert all(
        tool not in {"get_product_context", "get_sellers", "get_order_payments"}
        for _, tool in gateway.calls
    )


def test_complaint_time_selects_history_over_future_order() -> None:
    class HistoryGateway(FakeGateway):
        async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict:
            self.calls.append((case_id, tool_name))
            if tool_name == "get_order":
                data = {
                    "order_id": "real-order",
                    "order_status": "delivered",
                    "order_purchase_timestamp": "2018-05-11T09:00:00-03:00",
                }
            elif tool_name == "get_customer_history":
                data = {
                    "customer_unique_id": "customer-1",
                    "orders": [
                        {
                            "order_id": "real-order",
                            "order_status": "delivered",
                            "order_purchase_timestamp": "2017-12-20T09:00:00-03:00",
                            "order_delivered_carrier_date": "2017-12-22T09:00:00-03:00",
                            "order_delivered_customer_date": "2018-01-04T09:00:00-03:00",
                            "order_estimated_delivery_date": "2017-12-30T09:00:00-03:00",
                        },
                    ],
                }
            elif tool_name == "get_order_items":
                data = [
                    {
                        "order_item_id": "old-item",
                        "seller_id": "seller-1",
                        "price": "6.00",
                        "freight_value": "10.00",
                        "shipping_limit_date": "2017-12-23T09:00:00-03:00",
                    }
                ]
            elif tool_name == "get_payment_timeline":
                data = {
                    "events": [
                        {
                            "event_type": "captured",
                            "status": "confirmed",
                            "amount_brl": "16.00",
                            "event_at": "2017-12-20T10:00:00-03:00",
                        },
                        {
                            "event_type": "captured",
                            "status": "confirmed",
                            "amount_brl": "89.00",
                            "event_at": "2018-05-11T10:00:00-03:00",
                        },
                    ]
                }
            elif tool_name == "get_shipment_summary":
                data = {
                    "shipping_limits": [
                        {"seller_id": "seller-1", "shipping_limit_at": "2017-12-23T09:00:00-03:00"},
                    ]
                }
            elif tool_name == "get_policy":
                data = {
                    "rules": {
                        "late_delivery_logistics": {
                            "case_status": "action_required",
                            "refund_brl": 10,
                            "recommended_action": "refund_freight",
                            "responsible_parties": [
                                {"party_type": "logistics_provider", "party_id": None},
                            ],
                        }
                    }
                }
            else:
                raise AssertionError(tool_name)
            return {"evidence_ref": "ev_" + tool_name.ljust(24, "x"), "data": data}

    case = {
        "case_id": "L3B_CASE_001",
        "opened_at": "2018-01-01T09:00:00-03:00",
        "customer_request": {
            "claimed_order_id": "real-order",
            "claims": [
                {"claim_id": "claim-1", "topic": "late_delivery_logistics"},
            ],
        },
        "candidate_order_ids": ["real-order"],
        "customer_unique_id_hint": "customer-1",
        "policy_version": "EC_POLICY_V2",
    }
    output = asyncio.run(solve_case(case, HistoryGateway(), FakeTrace()))
    Contracts(Path(__file__).resolve().parents[1] / "contracts" / "schemas").validate_output(
        output, "history output"
    )
    assert output["assessment"]["primary_issue"] == "late_delivery_logistics"
    assert output["payment_analysis"]["captured_total_brl"] == 16
    assert output["data_conflicts"][0]["selected_source"] == "get_customer_history"
