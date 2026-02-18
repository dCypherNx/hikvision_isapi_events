"""Storage helpers for Hikvision ISAPI Events."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION


class HikvisionChannelTimeoutStore:
    """Persist per-channel timeout values for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict] = Store(
            hass,
            STORAGE_VERSION,
            f"hikvision_isapi_events.{entry_id}",
        )

    async def async_load(self) -> dict[int, int]:
        """Load persisted channel timeout values."""
        data = await self._store.async_load() or {}
        raw_timeouts = data.get("channel_timeouts", {})
        if not isinstance(raw_timeouts, dict):
            return {}

        parsed: dict[int, int] = {}
        for channel, value in raw_timeouts.items():
            try:
                parsed[int(channel)] = int(value)
            except (TypeError, ValueError):
                continue
        return parsed

    async def async_save(self, channel_timeouts: dict[int, int]) -> None:
        """Save channel timeout values."""
        await self._store.async_save(
            {
                "channel_timeouts": {
                    str(channel_id): int(seconds)
                    for channel_id, seconds in channel_timeouts.items()
                }
            }
        )
