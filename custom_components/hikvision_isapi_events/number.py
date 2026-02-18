"""Number platform for Hikvision ISAPI channel timeout values."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_RUNTIME,
    DOMAIN,
    MAX_OFF_DELAY_SECONDS,
    MIN_OFF_DELAY_SECONDS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up channel timeout number entities."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    manager = runtime.manager

    entities: dict[int, HikvisionChannelTimeoutNumber] = {}

    def _ensure_channel(channel_id: int) -> None:
        if channel_id in entities:
            return

        timeout = manager.get_channel_timeout(channel_id)
        entity = HikvisionChannelTimeoutNumber(manager, entry, channel_id, timeout)
        entities[channel_id] = entity
        async_add_entities([entity])

    for channel_id in manager.channel_ids():
        _ensure_channel(channel_id)

    manager.add_channel_listener(_ensure_channel)


class HikvisionChannelTimeoutNumber(NumberEntity):
    """Per-channel off timeout that applies immediately."""

    _attr_should_poll = False
    _attr_native_min_value = MIN_OFF_DELAY_SECONDS
    _attr_native_max_value = MAX_OFF_DELAY_SECONDS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "s"

    def __init__(
        self,
        manager,
        entry: ConfigEntry,
        channel_id: int,
        initial_timeout: int,
    ) -> None:
        self._manager = manager
        self._entry = entry
        self._channel_id = channel_id

        self._attr_name = f"Hikvision CH{channel_id} Off Timeout"
        self._attr_unique_id = f"{entry.entry_id}_ch{channel_id}_off_timeout"
        self.entity_id = f"number.hikvision_ch{channel_id}_off_timeout"
        self._attr_native_value = float(initial_timeout)

    @property
    def device_info(self) -> dict:
        """Attach number entity to channel device."""
        return {
            "identifiers": {self._manager.channel_identifier(self._channel_id)},
            "name": f"Hikvision CH{self._channel_id}",
            "manufacturer": "Hikvision",
            "model": "ISAPI Channel",
            "via_device": self._manager.dvr_identifier,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Persist and update timeout without restart."""
        seconds = int(value)
        await self._manager.async_set_channel_timeout(self._channel_id, seconds)
        self._attr_native_value = float(seconds)
        self.async_write_ha_state()
