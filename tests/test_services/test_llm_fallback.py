"""Tests for LLM fallback chain and rate-limit handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from agent.services import llm as llm_mod


def test_deployment_chain_dedupes_primary_and_fallbacks():
    with patch.object(llm_mod.settings, "azure_openai_deployment", "gpt-4o-mini"):
        with patch.object(llm_mod.settings, "azure_openai_planning_deployment", "gpt-4.1-mini"):
            with patch.object(
                llm_mod.settings,
                "azure_openai_planning_fallbacks",
                "gpt-4.1,gpt-4o,gpt-4.1-mini",
            ):
                with patch.object(llm_mod.settings, "azure_openai_fallback_deployments", ""):
                    chain = llm_mod.deployment_chain(role="planning")
    assert chain[0] == "gpt-4.1-mini"
    assert "gpt-4.1" in chain
    assert "gpt-4o" in chain
    assert len(chain) == len(set(chain))


def test_is_rate_limit_error_detects_429():
    exc = Exception("Error code: 429 - rate_limit_exceeded")
    assert llm_mod.is_rate_limit_error(exc)


def test_user_facing_llm_error_hides_raw_api():
    msg = llm_mod.user_facing_llm_error("implementation plan")
    assert "429" not in msg
    assert "rate_limit" not in msg.lower()
    assert "implementation plan" in msg


@pytest.mark.asyncio
async def test_invoke_llm_falls_back_on_rate_limit():
    rate_exc = Exception("429 rate_limit_exceeded")
    ok_response = MagicMock()
    ok_response.content = "ok"

    mock_llm_fail = MagicMock()
    mock_llm_fail.ainvoke = AsyncMock(side_effect=rate_exc)
    mock_llm_ok = MagicMock()
    mock_llm_ok.ainvoke = AsyncMock(return_value=ok_response)

    with patch.object(llm_mod, "deployment_chain", return_value=["gpt-4.1-mini", "gpt-4o"]):
        with patch.object(llm_mod, "get_llm", side_effect=[mock_llm_fail, mock_llm_ok]):
            with patch.object(llm_mod.settings, "llm_max_retries_per_deployment", 1):
                with patch.object(llm_mod.settings, "llm_retry_base_delay_sec", 0.01):
                    result = await llm_mod.invoke_llm(
                        [HumanMessage(content="hi")],
                        role="planning",
                    )
    assert result.content == "ok"
    assert mock_llm_ok.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_invoke_llm_raises_after_all_deployments_fail():
    rate_exc = Exception("429 too many requests")
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=rate_exc)

    with patch.object(llm_mod, "deployment_chain", return_value=["a", "b"]):
        with patch.object(llm_mod, "get_llm", return_value=mock_llm):
            with patch.object(llm_mod.settings, "llm_max_retries_per_deployment", 1):
                with patch.object(llm_mod.settings, "llm_retry_base_delay_sec", 0.01):
                    with pytest.raises(Exception, match="429"):
                        await llm_mod.invoke_llm([HumanMessage(content="hi")])
