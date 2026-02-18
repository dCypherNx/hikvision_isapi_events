"""Binary sensor platform for Hikvision ISAPI events."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_CHANNEL_ID,
    ATTR_LAST_EVENT_DATETIME,
    ATTR_LAST_EVENT_STATE,
    ATTR_LAST_EVENT_TYPE,
    ATTR_LAST_TARGET_TYPE,
    DATA_RUNTIME,
    DOMAIN,
    SENSOR_TYPES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hikvision binary sensors for an entry."""
    runtime = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    hub = runtime.hub

    entities: dict[tuple[int, str], HikvisionChannelBinarySensor] = {}

    def _ensure_channel(channel_id: int) -> None:
        new_entities: list[HikvisionChannelBinarySensor] = []
        for sensor_type in SENSOR_TYPES:
            key = (channel_id, sensor_type)
            if key in entities:
                continue
            sensor = HikvisionChannelBinarySensor(hub, entry, channel_id, sensor_type)
            entities[key] = sensor
            new_entities.append(sensor)
        if new_entities:
            async_add_entities(new_entities)

    for channel_id in hub.channel_ids():
        _ensure_channel(channel_id)

    hub.add_channel_listener(_ensure_channel)


class HikvisionChannelBinarySensor(BinarySensorEntity):
    """Event-driven binary sensor per channel/signal type."""

    _attr_should_poll = False

    def __init__(self, hub, entry: ConfigEntry, channel_id: int, sensor_type: str) -> None:
        self._hub = hub
        self._entry = entry
        self._channel_id = channel_id
        self._sensor_type = sensor_type
        self._remove_listener = None

        pretty = sensor_type.capitalize()
        self._attr_name = f"Hikvision CH{channel_id} {pretty}"
        self._attr_unique_id = f"{entry.entry_id}_ch{channel_id}_{sensor_type}"
        self.entity_id = f"binary_sensor.hikvision_ch{channel_id}_{sensor_type}"

    @property
    def is_on(self) -> bool:
        """Return the sensor state."""
        state = self._hub.get_state(self._channel_id)
        return {
            "motion": state.motion_on,
            "human": state.human_on,
            "vehicle": state.vehicle_on,
        }[self._sensor_type]

    @property
    def extra_state_attributes(self) -> dict:
        """Return attributes for diagnostics."""
        state = self._hub.get_state(self._channel_id)
        return {
            ATTR_CHANNEL_ID: self._channel_id,
            ATTR_LAST_EVENT_DATETIME: state.last_event_datetime,
            ATTR_LAST_EVENT_STATE: state.last_event_state,
            ATTR_LAST_TARGET_TYPE: state.last_target_type,
            ATTR_LAST_EVENT_TYPE: state.last_event_type,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to channel state changes."""

        def _update(channel_id: int) -> None:
            if channel_id != self._channel_id:
                return
            self.async_write_ha_state()

        self._remove_listener = self._hub.add_state_listener(_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from hub."""
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
