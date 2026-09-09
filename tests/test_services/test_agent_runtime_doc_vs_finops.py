"""Orchestration key: prepare-cost-document must not sticky-queue as FinOps."""

from agent.services.agent_runtime import infer_agent_key_from_message


def test_prepare_cost_document_routes_to_docs_not_finops():
    msg = "I want to prepare the beautiful document for cost details in subscription"
    assert infer_agent_key_from_message(msg) == "list_docs"


def test_plain_azure_costs_still_finops():
    assert infer_agent_key_from_message("azure costs") == "azure_finops"
    assert infer_agent_key_from_message("2") == ""
