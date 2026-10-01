"""Client for Dataplex Managed Context Sets via OneMCP toolset (cl/985213290)."""

import json
from typing import Any

import google.auth
import google.auth.exceptions
from google.auth.transport import requests as auth_requests
import requests

# Staging endpoint for testing; will be changed to prod endpoint in production
MCP_ENDPOINT = "https://staging-dataplex.sandbox.googleapis.com/mcp/managed-context-sets"
DEFAULT_OAUTH_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_REQUEST_TIMEOUT_SECONDS = 30


class ContextSetMcpClient:
    """Client for Dataplex Managed Context Sets via OneMCP tools."""

    def __init__(self, endpoint: str = MCP_ENDPOINT):
        self._endpoint = endpoint
        self._request_id = 0

        try:
            credentials, default_project = google.auth.default(
                scopes=DEFAULT_OAUTH_SCOPES
            )
        except google.auth.exceptions.DefaultCredentialsError as e:
            raise RuntimeError(
                "No Application Default Credentials found. Run "
                "'gcloud auth application-default login' first."
            ) from e

        quota_project = credentials.quota_project_id or default_project
        self._session = auth_requests.AuthorizedSession(credentials)
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        if quota_project:
            self._session.headers["X-Goog-User-Project"] = quota_project

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Dispatches a standard MCP tools/call request and unpacks result."""
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        response = self._session.post(
            self._endpoint,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            body = response.json()
        else:
            # Handle text/event-stream (SSE) format
            text = response.text
            body = None
            for line in text.splitlines():
                if line.startswith("data: "):
                    try:
                        body = json.loads(line[6:])
                        break
                    except Exception:
                        pass
            if body is None:
                try:
                    body = json.loads(text)
                except Exception:
                    body = {"raw": text}

        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
            msg = err.get("message", err) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"MCP tool '{tool_name}' error ({code}): {msg}")

        result = body.get("result", {}) if isinstance(body, dict) else body

        # Unpack standard MCP content envelope
        if isinstance(result, dict):
            if "structuredContent" in result:
                return result["structuredContent"]
            if "content" in result and result["content"]:
                first = result["content"][0]
                if isinstance(first, dict) and first.get("type") == "text":
                    text_val = first.get("text", "")
                    try:
                        return json.loads(text_val)
                    except (json.JSONDecodeError, TypeError):
                        return text_val
        return result

    def list_context_set_locations(self, project_id: str) -> list[str]:
        """Lists supported context set locations."""
        res = self.call_tool(
            "list_context_set_locations", {"project_id": project_id}
        )
        if isinstance(res, dict) and "locations" in res:
            return res["locations"]
        if isinstance(res, list):
            return res
        return res

    def upload_context_set(
        self,
        context_set: str,
        context_payload: str,
        description: str | None = None,
    ) -> Any:
        """Uploads a context set via OneMCP. Returns LRO operation name."""
        args: dict[str, Any] = {
            "context_set": context_set,
            "context_payload": context_payload,
        }
        if description:
            args["description"] = description
        return self.call_tool("upload_context_set", args)

    def get_context_set(self, context_set: str) -> Any:
        """Retrieves a context set via OneMCP."""
        return self.call_tool("get_context_set", {"context_set": context_set})

    def delete_context_set(self, context_set: str) -> Any:
        """Deletes a context set via OneMCP. Returns LRO operation name."""
        return self.call_tool("delete_context_set", {"context_set": context_set})

    def get_operation(
        self,
        project_id: str,
        location: str,
        operation_id: str,
    ) -> Any:
        """Polls an LRO operation via OneMCP."""
        return self.call_tool(
            "get_operation",
            {
                "project_id": project_id,
                "location": location,
                "operation_id": operation_id,
            },
        )
