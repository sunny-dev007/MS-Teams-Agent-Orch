import pytest


@pytest.mark.asyncio
async def test_whatsapp_verify_success(client):
    resp = await client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.challenge": "123456",
        "hub.verify_token": "test-verify-token",
    })
    assert resp.status_code == 200
    assert resp.json() == 123456


@pytest.mark.asyncio
async def test_whatsapp_verify_failure(client):
    resp = await client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.challenge": "123456",
        "hub.verify_token": "wrong-token",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_whatsapp_receive_status_update(client):
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "statuses": [{"id": "msg1", "status": "delivered"}]
                }
            }]
        }]
    }
    resp = await client.post("/webhooks/whatsapp", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
