from agent.services.whatsapp import parse_incoming_message


def test_parse_incoming_message_valid():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "919876543210",
                        "id": "wamid.abc123",
                        "text": {"body": "Check my emails"},
                        "timestamp": "1700000000",
                    }],
                    "contacts": [{
                        "profile": {"name": "Test User"},
                    }],
                }
            }]
        }]
    }
    result = parse_incoming_message(payload)
    assert result is not None
    assert result["phone"] == "919876543210"
    assert result["message"] == "Check my emails"
    assert result["name"] == "Test User"


def test_parse_incoming_message_no_messages():
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "statuses": [{"status": "delivered"}]
                }
            }]
        }]
    }
    result = parse_incoming_message(payload)
    assert result is None


def test_parse_incoming_message_malformed():
    result = parse_incoming_message({})
    assert result is None
