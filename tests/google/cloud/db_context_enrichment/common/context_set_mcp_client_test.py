import json
from unittest.mock import MagicMock
import pytest
import requests

from google.cloud.db_context_enrichment.common import context_set_mcp_client
from google.cloud.db_context_enrichment.common.context_set_mcp_client import (
    ContextSetMcpClient,
)


def _make_response(status_code: int, body=None, content_type: str = "application/json"):
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.headers = {"content-type": content_type}
    if body is None:
        response.content = b""
        response.text = ""
        response.json.side_effect = ValueError("no body")
    elif isinstance(body, str):
        response.content = body.encode()
        response.text = body
        response.json.side_effect = ValueError("not json")
    else:
        encoded = json.dumps(body)
        response.content = encoded.encode()
        response.text = encoded
        response.json.return_value = body

    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status_code}", response=response
        )
    return response


@pytest.fixture
def client(monkeypatch):
    fake_credentials = MagicMock()
    fake_credentials.quota_project_id = "test-project"
    fake_credentials.valid = True

    monkeypatch.setattr(
        "google.auth.default",
        lambda scopes=None: (fake_credentials, None),
    )
    mock_session = MagicMock()
    mock_session.headers = {}
    monkeypatch.setattr(
        context_set_mcp_client.auth_requests,
        "AuthorizedSession",
        lambda creds: mock_session,
    )
    return ContextSetMcpClient()


def test_call_tool_json_content_unpacking(client):
    expected_inner = {"locations": ["us-central1"]}
    fake_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [
                {"type": "text", "text": json.dumps(expected_inner)}
            ]
        },
    }
    client._session.post.return_value = _make_response(200, fake_body)
    res = client.call_tool("list_context_set_locations", {"project_id": "test-project"})
    assert res == expected_inner


def test_call_tool_structured_content(client):
    expected_inner = {"name": "op1", "done": True}
    fake_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "structuredContent": expected_inner
        },
    }
    client._session.post.return_value = _make_response(200, fake_body)
    res = client.call_tool("get_operation", {"name": "op1"})
    assert res == expected_inner


def test_call_tool_sse_unpacking(client):
    expected_inner = {"payload": "test-data"}
    sse_text = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': expected_inner})}\n\n"
    client._session.post.return_value = _make_response(200, sse_text, content_type="text/event-stream")
    res = client.call_tool("get_context_set", {"context_set": "test-cs"})
    assert res == expected_inner


def test_call_tool_error_raised(client):
    fake_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": 404,
            "message": "Resource not found",
        },
    }
    client._session.post.return_value = _make_response(200, fake_body)
    with pytest.raises(RuntimeError, match="MCP tool 'test_tool' error"):
        client.call_tool("test_tool", {})


def test_convenience_methods(client, monkeypatch):
    monkeypatch.setattr(client, "call_tool", lambda name, args: {"tool": name, "args": args})

    locs = client.list_context_set_locations("p")
    assert locs == {"tool": "list_context_set_locations", "args": {"project_id": "p"}}

    upload = client.upload_context_set("cs", "payload", "desc")
    assert upload["tool"] == "upload_context_set"
    assert upload["args"]["description"] == "desc"

    get = client.get_context_set("cs")
    assert get["tool"] == "get_context_set"

    delete = client.delete_context_set("cs")
    assert delete["tool"] == "delete_context_set"

    op = client.get_operation("p", "l", "op1")
    assert op["tool"] == "get_operation"
