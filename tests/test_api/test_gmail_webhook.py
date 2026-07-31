import base64
import json

import pytest


@pytest.mark.asyncio
async def test_gmail_push_valid(client):
    data = base64.b64encode(json.dumps({"historyId": "12345"}).encode()).decode()
    payload = {"message": {"data": data}}

    resp = await client.post("/webhooks/gmail", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_gmail_push_invalid(client):
    resp = await client.post("/webhooks/gmail", json={"message": {}})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
