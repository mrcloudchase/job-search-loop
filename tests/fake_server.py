"""A stand-in for the harness-interface server: enough of POST /responses (streamed), /files, /diff and
/cancel for a loop's tests. Each reply is scripted by the test; every request is kept for asserting on."""
from __future__ import annotations

import json

import httpx


class FakeServer:
    def __init__(self, replies):
        self.replies = list(replies)                  # per turn: {"files": {path: text}, "answer": str, "cost": float|None, "status": str}
        self.requests: list[dict] = []                # the POST /responses bodies, in order
        self.files: dict[str, dict[str, str]] = {}    # response id -> files
        self.cancelled: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/responses":
            body = json.loads(request.content)
            self.requests.append(body)
            reply = self.replies.pop(0)
            rid = f"resp_{len(self.requests)}"
            self.files[rid] = {**self.files.get(body.get("previous_response_id"), {}), **reply.get("files", {})}   # a conversation shares its workspace
            record = {"id": rid, "model": body["model"], "status": reply.get("status", "completed"),
                      "usage": reply.get("usage"), "output": [{"content": [{"type": "output_text", "text": reply.get("answer", "")}]}],
                      "harness": {"name": body["model"], "latency_s": 1.0, "cost_usd": reply.get("cost"),
                                  "files": sorted(reply.get("files", {}))}}
            sse = (f"event: response.created\ndata: {json.dumps({'response': {'id': rid}})}\n\n"
                   f"event: response.completed\ndata: {json.dumps({'response': record})}\n\n")
            return httpx.Response(200, text=sse)
        if request.method == "POST" and path.endswith("/cancel"):
            self.cancelled.append(path.split("/")[2])
            return httpx.Response(200, json={"cancelled": True})
        if request.method == "GET" and path.endswith("/files"):
            return httpx.Response(200, json=sorted(self.files.get(path.split("/")[2], {})))
        parts = path.split("/")                       # /responses/{id}/files/{path} or /responses/{id}/diff
        if len(parts) > 4 and parts[3] == "files":
            text = self.files.get(parts[2], {}).get("/".join(parts[4:]))
            return httpx.Response(200, text=text) if text is not None else httpx.Response(404)
        return httpx.Response(404)
