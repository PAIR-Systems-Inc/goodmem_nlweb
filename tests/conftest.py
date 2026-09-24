"""Fixtures: the async SDK driven over replayed live-server bytes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from goodmem import AsyncGoodmem
import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def ndjson_events(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURES / name).read_bytes().decode().strip().split("\n")
        if line.strip()
    ]


def ndjson_response(events: list[dict[str, Any]]) -> httpx.Response:
    body = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
    return httpx.Response(
        200, content=body, headers={"Content-Type": "application/x-ndjson; charset=utf-8"}
    )


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []
        self._routes: list[tuple[str, str, httpx.Response]] = []

    def route(self, method: str, path: str, response: httpx.Response) -> None:
        self._routes.append((method.upper(), path, response))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        try:
            self.bodies.append(json.loads(request.content) if request.content else None)
        except ValueError:
            self.bodies.append(request.content)
        for method, path, response in self._routes:
            if request.method == method and request.url.path == path:
                return response
        raise AssertionError(f"unrouted {request.method} {request.url.path}")

    @property
    def last_body(self) -> Any:
        return self.bodies[-1]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def client(recorder: Recorder) -> AsyncGoodmem:
    """A real async SDK client whose transport replays fixtures.

    The SDK refuses base_url/api_key alongside an injected client, so both
    belong on the httpx client.
    """
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handler),
        base_url="https://goodmem.test",
        headers={"x-api-key": "gm_test_key_not_a_real_credential"},
    )
    return AsyncGoodmem(http_client=http_client)
