"""Channel discovery helpers for Hikvision ISAPI devices."""

from __future__ import annotations

import logging

from .const import CHANNEL_DISCOVERY_PATHS
from .parsing import parse_channel_ids

_LOGGER = logging.getLogger(__name__)


async def discover_channels(client) -> list[int]:
    """Discover channel IDs using known ISAPI endpoints."""
    for path in CHANNEL_DISCOVERY_PATHS:
        try:
            status, body = await client.fetch_text(path)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Channel discovery endpoint failed %s: %s", path, err)
            continue

        if status != 200:
            _LOGGER.debug("Channel discovery endpoint returned %s for %s", status, path)
            continue

        channel_ids = parse_channel_ids(body)
        if channel_ids:
            _LOGGER.debug("Discovered channels from %s: %s", path, channel_ids)
            return channel_ids

    _LOGGER.debug("No channels discovered from known endpoints")
    return []
