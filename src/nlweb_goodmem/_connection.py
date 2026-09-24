"""SDK client ownership.

Ported from the AutoGen integration; the cancellation-token plumbing is
removed because NLWeb has no equivalent.
"""

from __future__ import annotations

from typing import cast

from goodmem import AsyncGoodmem
from typing_extensions import Self

from nlweb_goodmem._typing import AsyncGoodmemClient


class GoodMemConnection:
    """Owns an ``AsyncGoodmem`` client, or borrows a caller-supplied one.

    An injected client keeps its own server, credentials and TLS settings;
    this class never closes it. Otherwise one client is created lazily and
    closed by :meth:`close`. There is no process-wide client cache.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        verify_ssl: bool | str = True,
        timeout: float = 60.0,
        client: AsyncGoodmem | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._injected = client
        self._owned: AsyncGoodmem | None = None

    @property
    def owns_client(self) -> bool:
        return self._injected is None

    def client(self) -> AsyncGoodmemClient:
        if self._injected is not None:
            return cast(AsyncGoodmemClient, self._injected)
        if self._owned is None:
            if not self._base_url or not self._api_key:
                raise ValueError(
                    "GoodMem base_url and api_key are required when no client is injected."
                )
            self._owned = AsyncGoodmem(
                base_url=self._base_url.rstrip("/"),
                api_key=self._api_key,
                verify=self._verify_ssl,
                timeout=self._timeout,
            )
        return cast(AsyncGoodmemClient, self._owned)

    async def close(self) -> None:
        if self._owned is not None:
            await self._owned.close()
            self._owned = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
