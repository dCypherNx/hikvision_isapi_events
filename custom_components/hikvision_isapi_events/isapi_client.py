"""Async ISAPI client for Hikvision event stream."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import hashlib
import logging
import os
import random
import re
from typing import Any

import aiohttp

from .const import ALERT_STREAM_PATH, DEVICE_INFO_PATH
from .parsing import AlertStreamParser, parse_event_notification

_LOGGER = logging.getLogger(__name__)

_DIGEST_PAIR_RE = re.compile(r"(\w+)=(?:\"([^\"]*)\"|([^,]+))")


class DigestAuthState:
    """State holder for HTTP Digest authentication."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._challenge: dict[str, str] = {}
        self._nc = 0

    def update_from_header(self, header: str | None) -> None:
        """Parse WWW-Authenticate digest challenge."""
        if not header or "digest" not in header.lower():
            return

        payload = header.split(" ", 1)[1] if " " in header else header
        challenge: dict[str, str] = {}
        for match in _DIGEST_PAIR_RE.finditer(payload):
            key = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            challenge[key.lower()] = value.strip()
        if "realm" in challenge and "nonce" in challenge:
            self._challenge = challenge
            self._nc = 0

    def build_authorization(self, method: str, uri: str) -> str | None:
        """Build digest Authorization header from current challenge."""
        challenge = self._challenge
        realm = challenge.get("realm")
        nonce = challenge.get("nonce")
        if not realm or not nonce:
            return None

        qop = challenge.get("qop", "auth")
        opaque = challenge.get("opaque")
        algorithm = challenge.get("algorithm", "MD5")
        if algorithm.upper() != "MD5":
            _LOGGER.debug("Unsupported digest algorithm %s, forcing MD5", algorithm)

        self._nc += 1
        nc_value = f"{self._nc:08x}"
        cnonce = hashlib.md5(os.urandom(16) + str(random.random()).encode(), usedforsecurity=False).hexdigest()[:16]  # noqa: S324

        ha1_raw = f"{self._username}:{realm}:{self._password}"
        ha1 = hashlib.md5(ha1_raw.encode(), usedforsecurity=False).hexdigest()  # noqa: S324

        ha2_raw = f"{method}:{uri}"
        ha2 = hashlib.md5(ha2_raw.encode(), usedforsecurity=False).hexdigest()  # noqa: S324

        response_raw = f"{ha1}:{nonce}:{nc_value}:{cnonce}:auth:{ha2}"
        response = hashlib.md5(response_raw.encode(), usedforsecurity=False).hexdigest()  # noqa: S324

        parts = [
            f'username="{self._username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            "algorithm=MD5",
            f'response="{response}"',
            "qop=auth",
            f"nc={nc_value}",
            f'cnonce="{cnonce}"',
        ]
        if opaque:
            parts.append(f'opaque="{opaque}"')

        return "Digest " + ", ".join(parts)


class HikvisionIsapiClient:
    """Client for non-blocking HTTP Digest calls and event streaming."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        use_ssl: bool,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._scheme = "https" if use_ssl else "http"
        self._use_ssl = use_ssl
        self._host = host
        self._port = port
        self._digest = DigestAuthState(username, password)

    @property
    def base_url(self) -> str:
        """Base URL for requests."""
        return f"{self._scheme}://{self._host}:{self._port}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: aiohttp.ClientTimeout | None = None,
        allow_retry_unauthorized: bool = True,
    ) -> aiohttp.ClientResponse:
        headers: dict[str, str] = {}
        auth_header = self._digest.build_authorization(method, path)
        if auth_header:
            headers["Authorization"] = auth_header

        response = await self._session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=timeout,
            ssl=self._use_ssl,
        )

        if response.status != 401:
            return response

        self._digest.update_from_header(response.headers.get("WWW-Authenticate"))
        if not allow_retry_unauthorized:
            return response

        await response.release()

        retry_headers: dict[str, str] = {}
        retry_auth = self._digest.build_authorization(method, path)
        if retry_auth:
            retry_headers["Authorization"] = retry_auth

        return await self._session.request(
            method,
            f"{self.base_url}{path}",
            headers=retry_headers,
            timeout=timeout,
            ssl=self._use_ssl,
        )

    async def fetch_text(self, path: str) -> tuple[int, str]:
        """Fetch endpoint and return status with text payload."""
        timeout = aiohttp.ClientTimeout(total=15)
        async with await self._request("GET", path, timeout=timeout) as response:
            return response.status, await response.text()

    async def validate_device_info(self) -> bool:
        """Validate access by calling /ISAPI/System/deviceInfo."""
        status, _body = await self.fetch_text(DEVICE_INFO_PATH)
        return status == 200

    async def stream_alerts(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        stop_event,
        *,
        timeout_seconds: int = 90,
    ) -> None:
        """Connect to alertStream and invoke callback for each parsed event."""
        timeout = aiohttp.ClientTimeout(total=None, sock_read=timeout_seconds)

        async with await self._request("GET", ALERT_STREAM_PATH, timeout=timeout) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"alertStream returned {response.status}: {body[:200]}")

            parser = AlertStreamParser()
            async for chunk in response.content.iter_any():
                if stop_event.is_set():
                    return
                if not chunk:
                    continue
                for xml_doc in parser.feed(chunk):
                    event = parse_event_notification(xml_doc)
                    if event is not None:
                        await callback(event)
