"""Azure FinOps Agent — flag isolation, formatting, recommendations."""

from __future__ import annotations

import pytest

from agent.agents import azure_finops_agent as agent
from agent.config import Settings
from agent.services import azure_finops


@pytest.mark.asyncio
async def test_finops_disabled_returns_skipped(monkeypatch):
    monkeypatch.setattr(agent.settings, "enable_azure_finops_agent", False)
    out = await agent.run_azure_finops(
        {
            "whatsapp_phone": "teams:user-1",
            "user_message": "azure subscriptions",
            "session_awaiting": "",
            "session_data": {},
        }
    )
    assert out["status"] == "skipped"
    assert out["handled_by"] == agent.AGENT_NAME
    text = (out.get("notification_text") or "").lower()
    assert "turned off" in text or "disabled" in text
    assert "enable_azure_finops_agent=true" in text


@pytest.mark.asyncio
async def test_finops_missing_arm_credentials(monkeypatch):
    monkeypatch.setattr(agent.settings, "enable_azure_finops_agent", True)
    monkeypatch.setattr(azure_finops, "arm_configured", lambda: False)
    out = await agent.run_azure_finops(
        {
            "whatsapp_phone": "teams:user-1",
            "user_message": "azure subscriptions",
            "session_awaiting": "",
            "session_data": {},
        }
    )
    assert out["status"] == "failed"
    assert "credentials" in (out.get("notification_text") or "").lower()


def test_build_recommendations_flags_public_ip_and_high_share():
    resources = [
        {
            "name": "pip-demo",
            "type": "Microsoft.Network/publicIPAddresses",
            "rg": "rg-demo",
            "sku": "",
        },
        {
            "name": "plan-b1",
            "type": "Microsoft.Web/serverfarms",
            "rg": "rg-demo",
            "sku": "B1",
        },
    ]
    costs = [
        {"name": "/subscriptions/x/resourceGroups/rg-demo/providers/Microsoft.Web/serverfarms/plan-b1", "cost": 80.0, "currency": "USD"},
        {"name": "other", "cost": 20.0, "currency": "USD"},
    ]
    tips = azure_finops.build_recommendations(
        resources,
        cost_by_resource=costs,
        cost_by_rg=[{"name": "rg-demo", "cost": 100.0, "currency": "USD"}],
    )
    titles = " ".join(t["title"] for t in tips).lower()
    assert "public ip" in titles or "pip-demo" in titles
    assert any(t.get("severity") == "high" for t in tips)


def test_format_subscriptions_table():
    text = agent._format_subscriptions(
        [
            {"id": "aaa", "name": "Pay-As-You-Go", "state": "Enabled"},
            {"id": "bbb", "name": "Azure subscription 1", "state": "Enabled"},
        ]
    )
    assert "Pay-As-You-Go" in text
    assert "| 1 |" in text
    assert "subscription" in text.lower()


@pytest.mark.asyncio
async def test_planner_routes_azure_finops(monkeypatch):
    from agent.planner import agent as planner

    state = {
        "user_message": "show my azure subscriptions",
        "whatsapp_phone": "teams:user-1",
    }

    async def _noop_soft(*a, **k):
        return ""

    monkeypatch.setattr(
        "agent.services.conversation_context.soft_fabric_switch",
        _noop_soft,
        raising=False,
    )
    # Session lookups should not blow up planner routing
    async def _fake_session(_phone):
        return {"awaiting": None, "data": {}}

    monkeypatch.setattr("agent.core.session.get_session", _fake_session, raising=False)
    out = await planner.plan(state)
    assert out.get("intent") == "azure_finops"


def test_settings_flag_defaults_off():
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
    )
    # Constructing without env should keep additive flag false
    assert getattr(s, "enable_azure_finops_agent", False) is False


def test_mutations_flag_defaults_off():
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert getattr(s, "enable_azure_finops_mutations", False) is False
    assert getattr(s, "enable_azure_finops_allow_delete", False) is False


def test_mutation_helpers_parse_and_refuse():
    from agent.services import azure_finops_mutations as mut

    assert mut.is_scan_request("deep scan my azure")
    assert mut.is_resize_request("please downgrade this plan to B1")
    assert mut.parse_apply_plan_id("APPLY PLAN sku-abc12345") == "sku-abc12345"
    assert mut.is_reject_plan("REJECT PLAN")
    assert mut.is_mutable_type("Microsoft.Web/serverFarms")
    assert not mut.mutations_enabled()
    assert "delete" in mut.refuse_delete_message().lower()
    assert mut.suggest_downgrade_sku("Microsoft.Web/serverFarms", "B1") in {"D1", "F1"}
    assert mut.resolve_target_sku("Microsoft.Web/serverFarms", "F1") is not None


@pytest.mark.asyncio
async def test_delete_refused_even_when_agent_on(monkeypatch):
    monkeypatch.setattr(agent.settings, "enable_azure_finops_agent", True)
    monkeypatch.setattr(azure_finops, "arm_configured", lambda: True)
    monkeypatch.setattr(agent.settings, "enable_azure_finops_mutations", True)
    monkeypatch.setattr(agent.settings, "enable_azure_finops_allow_delete", False)

    async def _fake_subs():
        return [{"id": "sub-1", "name": "PayGo", "state": "Enabled"}]

    monkeypatch.setattr(azure_finops, "list_subscriptions", _fake_subs)

    out = await agent.run_azure_finops(
        {
            "whatsapp_phone": "teams:user-1",
            "user_message": "delete this app service plan",
            "session_awaiting": agent.AWAITING_ACTION,
            "session_data": {
                "azure_finops_sub": {"id": "sub-1", "name": "PayGo"},
            },
        }
    )
    text = (out.get("notification_text") or "").lower()
    assert "delete" in text
    assert "blocked" in text or "refused" in text or "not" in text


@pytest.mark.asyncio
async def test_apply_plan_refused_when_mutations_off(monkeypatch):
    monkeypatch.setattr(agent.settings, "enable_azure_finops_agent", True)
    monkeypatch.setattr(azure_finops, "arm_configured", lambda: True)
    monkeypatch.setattr(agent.settings, "enable_azure_finops_mutations", False)

    plan = {
        "id": "sku-deadbeef",
        "action": "sku_change",
        "resource_name": "plan-demo",
        "from_sku_label": "B1",
        "to_sku_label": "F1",
        "destructive": False,
    }
    out = await agent.run_azure_finops(
        {
            "whatsapp_phone": "teams:user-1",
            "user_message": "APPLY PLAN sku-deadbeef",
            "session_awaiting": agent.AWAITING_MUTATE,
            "session_data": {
                "azure_finops_sub": {"id": "sub-1", "name": "PayGo"},
                "azure_finops_plan": plan,
            },
        }
    )
    text = (out.get("notification_text") or "").lower()
    assert "mutation" in text or "disabled" in text or "refusing" in text or "off" in text


@pytest.mark.asyncio
async def test_word_costs_keeps_action_awaiting(monkeypatch):
    """Regression: "costs" must not reset to subscription picker mid-flow."""
    monkeypatch.setattr(agent.settings, "enable_azure_finops_agent", True)
    monkeypatch.setattr(azure_finops, "arm_configured", lambda: True)

    async def _fake_costs_result(sid, *, group_by="ResourceGroupName", days=None):
        return {
            "ok": True,
            "rows": [{"name": "feapp", "cost": 42.0, "currency": "INR"}],
            "error": None,
        }

    async def _noop_save(*a, **k):
        return None

    monkeypatch.setattr(azure_finops, "query_costs_result", _fake_costs_result)
    monkeypatch.setattr("agent.core.session.save_session", _noop_save, raising=False)
    monkeypatch.setattr(agent, "save_session", _noop_save, raising=False)

    out = await agent.run_azure_finops(
        {
            "whatsapp_phone": "teams:user-1",
            "user_message": "costs",
            "session_awaiting": agent.AWAITING_ACTION,
            "session_data": {
                "azure_finops_sub": {"id": "sub-1", "name": "Pay-As-You-Go"},
            },
        }
    )
    assert out.get("session_awaiting") == agent.AWAITING_ACTION
    assert "feapp" in (out.get("notification_text") or "")
    assert "choose a subscription" not in (out.get("notification_text") or "").lower()


@pytest.mark.asyncio
async def test_menu_word_stays_on_action(monkeypatch):
    monkeypatch.setattr(agent.settings, "enable_azure_finops_agent", True)
    monkeypatch.setattr(azure_finops, "arm_configured", lambda: True)

    async def _noop_save(*a, **k):
        return None

    monkeypatch.setattr("agent.core.session.save_session", _noop_save, raising=False)

    out = await agent.run_azure_finops(
        {
            "whatsapp_phone": "teams:user-1",
            "user_message": "menu",
            "session_awaiting": agent.AWAITING_ACTION,
            "session_data": {
                "azure_finops_sub": {"id": "sub-1", "name": "Pay-As-You-Go"},
            },
        }
    )
    assert out.get("session_awaiting") == agent.AWAITING_ACTION
    text = out.get("notification_text") or ""
    assert "Pay-As-You-Go" in text
    assert "1" in text and "Cost" in text


def test_format_costs_distinguishes_api_failure():
    # Chat must stay soft — no raw ARM / 429 / URL leakage even if error= is passed.
    fail = agent._format_costs(
        [],
        title="Cost by RG",
        days=30,
        ok=False,
        error="ARM error '429: Too many requests' for url 'https://management.azure.com/x'",
        error_kind="throttled",
        user_note=azure_finops.user_safe_cost_note("throttled"),
    )
    empty = agent._format_costs([], title="Cost by RG", days=30, ok=True)
    low = fail.lower()
    assert "429" not in fail
    assert "management.azure.com" not in fail
    assert "arm error" not in low
    assert "busy" in low or "delayed" in low or "unavailable" in low
    assert "cost management reader" not in low  # RBAC hint is for forbidden only
    assert "no rows" in empty.lower() or "zero" in empty.lower() or "last" in empty.lower()
    assert "busy" not in empty.lower()


@pytest.mark.asyncio
async def test_query_costs_result_throttled_is_chat_safe(monkeypatch):
    async def _boom(*_a, **_k):
        raise Exception(
            "ARM error '429: {\"error\":{\"code\":\"429\",\"message\":\"Too many requests. Please retry.\"}}' "
            "for url 'https://management.azure.com/subscriptions/x/providers/Microsoft.CostManagement/query'"
        )

    azure_finops.clear_cost_query_cache()
    monkeypatch.setattr(azure_finops, "arm_request", _boom)
    out = await azure_finops.query_costs_result("sub-1", group_by="ResourceGroupName")
    assert out["ok"] is False
    assert out.get("error") in (None, "")
    assert out.get("error_kind") == "throttled"
    note = out.get("user_note") or ""
    assert "429" not in note
    assert "management.azure.com" not in note
    assert "Cost Management Reader" not in note


def test_recommendations_rank_paid_spend_over_free_sku():
    tips = azure_finops.build_recommendations(
        [
            {"name": "plan-f1", "type": "Microsoft.Web/serverFarms", "rg": "rg-dev", "sku": "F1"},
            {"name": "plan-paid", "type": "Microsoft.Web/serverFarms", "rg": "feapp", "sku": "S1"},
        ],
        cost_by_resource=[
            {
                "name": "/subscriptions/x/resourceGroups/feapp/providers/Microsoft.Web/serverFarms/plan-paid",
                "cost": 3306.0,
                "currency": "INR",
            },
            {"name": "other", "cost": 50.0, "currency": "INR"},
        ],
        cost_by_rg=[
            {"name": "feapp", "cost": 3306.0, "currency": "INR"},
            {"name": "rg-dev", "cost": 0.0, "currency": "INR"},
        ],
    )
    titles = [t["title"] for t in tips]
    assert any("feapp" in t or "plan-paid" in t for t in titles[:3])
    f1 = [t for t in tips if "F1" in t["title"] or "plan-f1" in t["title"]]
    assert f1
    assert all(t.get("severity") in {"info", "low"} for t in f1)


@pytest.mark.asyncio
async def test_planner_preserves_finops_awaiting_on_costs(monkeypatch):
    from agent.planner import agent as planner

    async def _fake_session(_phone):
        return {
            "awaiting": "azure_finops_action_pick",
            "data": {"azure_finops_sub": {"id": "sub-1", "name": "PayGo"}},
        }

    # Planner imports get_session into its module namespace
    monkeypatch.setattr(planner, "get_session", _fake_session)
    out = await planner.plan(
        {
            "user_message": "costs",
            "whatsapp_phone": "teams:user-1",
        }
    )
    assert out.get("intent") == "azure_finops"
    assert out.get("session_awaiting") == "azure_finops_action_pick"
