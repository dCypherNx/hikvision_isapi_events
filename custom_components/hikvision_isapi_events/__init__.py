"""Hikvision ISAPI Events integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DEFAULT_OFF_DELAY_SECONDS,
    CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES,
    CONF_RECONNECT_DELAY_SECONDS,
    CONF_USE_SSL,
    DATA_RUNTIME,
    DEFAULT_OFF_DELAY_SECONDS,
    DEFAULT_RECONNECT_DELAY_SECONDS,
    DOMAIN,
    DVR_DEVICE_KEY,
    EVENT_TYPE_VMD,
    MAX_OFF_DELAY_SECONDS,
    MIN_OFF_DELAY_SECONDS,
    PLATFORMS,
)
from .discovery import discover_channels
from .isapi_client import HikvisionIsapiClient
from .storage import HikvisionChannelTimeoutStore

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ChannelState:
    """Current state for a channel."""

    channel_id: int
    motion_on: bool = False
    human_on: bool = False
    vehicle_on: bool = False
    last_event_datetime: str | None = None
    last_event_state: str | None = None
    last_target_type: str | None = None
    last_event_type: str | None = None
    off_timer: asyncio.TimerHandle | None = None


class ChannelManager:
    """Central state/event manager consumed by entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        timeout_store: HikvisionChannelTimeoutStore,
        initial_timeouts: dict[int, int],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._timeout_store = timeout_store
        self._states: dict[int, ChannelState] = {}
        self._entity_listeners: list[Callable[[int], None]] = []
        self._channel_listeners: list[Callable[[int], None]] = []
        self._timeouts = {
            channel_id: self._clamp_timeout(seconds)
            for channel_id, seconds in initial_timeouts.items()
        }

    @property
    def dvr_identifier(self) -> tuple[str, str, str]:
        """Return parent DVR device identifier tuple."""
        return (DOMAIN, self.entry.entry_id, DVR_DEVICE_KEY)

    def channel_identifier(self, channel_id: int) -> tuple[str, str, str]:
        """Return channel device identifier tuple."""
        return (DOMAIN, self.entry.entry_id, str(channel_id))

    def get_state(self, channel_id: int) -> ChannelState:
        """Get/create channel state."""
        state = self._states.get(channel_id)
        if state is not None:
            return state

        state = ChannelState(channel_id=channel_id)
        self._states[channel_id] = state
        for callback in self._channel_listeners:
            callback(channel_id)
        return state

    def channel_ids(self) -> list[int]:
        """Return sorted known channels."""
        return sorted(self._states)

    def add_channel_listener(self, callback: Callable[[int], None]) -> Callable[[], None]:
        """Listen for newly-created channels."""
        self._channel_listeners.append(callback)

        def _remove() -> None:
            self._channel_listeners.remove(callback)

        return _remove

    def add_state_listener(self, callback: Callable[[int], None]) -> Callable[[], None]:
        """Listen for channel state updates."""
        self._entity_listeners.append(callback)

        def _remove() -> None:
            self._entity_listeners.remove(callback)

        return _remove

    def _notify_state(self, channel_id: int) -> None:
        for callback in self._entity_listeners:
            callback(channel_id)

    @staticmethod
    def _clamp_timeout(seconds: int) -> int:
        return max(MIN_OFF_DELAY_SECONDS, min(MAX_OFF_DELAY_SECONDS, int(seconds)))

    def get_channel_timeout(self, channel_id: int) -> int:
        """Return timeout for one channel, falling back to global default."""
        default_delay = int(
            self.entry.data.get(CONF_DEFAULT_OFF_DELAY_SECONDS, DEFAULT_OFF_DELAY_SECONDS)
        )
        return self._timeouts.get(channel_id, self._clamp_timeout(default_delay))

    async def async_set_channel_timeout(self, channel_id: int, seconds: int) -> None:
        """Persist timeout and apply immediately."""
        self._timeouts[channel_id] = self._clamp_timeout(seconds)
        await self._timeout_store.async_save(self._timeouts)

    def _cancel_timer(self, state: ChannelState) -> None:
        if state.off_timer and not state.off_timer.cancelled():
            state.off_timer.cancel()
        state.off_timer = None

    def _timer_expire(self, channel_id: int) -> None:
        state = self.get_state(channel_id)
        state.motion_on = False
        state.human_on = False
        state.vehicle_on = False
        state.last_event_state = "inactive"
        self._cancel_timer(state)
        self._notify_state(channel_id)

    def _schedule_off(self, channel_id: int) -> None:
        state = self.get_state(channel_id)
        self._cancel_timer(state)

        delay = self.get_channel_timeout(channel_id)
        if delay <= 0:
            return

        state.off_timer = self.hass.loop.call_later(delay, self._timer_expire, channel_id)

    def process_event(self, event: dict) -> None:
        """Process parsed VMD event."""
        if event.get("event_type") != EVENT_TYPE_VMD:
            return

        channel_id = event.get("channel_id")
        if not isinstance(channel_id, int):
            return

        state = self.get_state(channel_id)
        event_state = (event.get("event_state") or "").lower()
        target_type = (event.get("target_type") or "").lower()

        state.last_event_datetime = event.get("date_time")
        state.last_event_state = event_state or None
        state.last_target_type = target_type or None
        state.last_event_type = event.get("event_type")

        if event_state == "active":
            state.motion_on = True
            if target_type == "human":
                state.human_on = True
            elif target_type == "vehicle":
                state.vehicle_on = True
            self._schedule_off(channel_id)
        elif event_state == "inactive":
            state.motion_on = False
            state.human_on = False
            state.vehicle_on = False
            self._cancel_timer(state)

        self._notify_state(channel_id)

    def add_discovered_channels(self, channels: list[int]) -> None:
        """Register discovered channel IDs before events arrive."""
        for channel_id in channels:
            self.get_state(channel_id)

    def shutdown(self) -> None:
        """Cancel all timers."""
        for state in self._states.values():
            self._cancel_timer(state)


def _parse_overrides(raw: str) -> dict[int, int]:
    overrides: dict[int, int] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        try:
            channel_id = int(left.strip())
            seconds = int(right.strip())
        except ValueError:
            continue
        overrides[channel_id] = max(MIN_OFF_DELAY_SECONDS, min(MAX_OFF_DELAY_SECONDS, seconds))
    return overrides


@dataclass(slots=True)
class RuntimeData:
    """In-memory runtime objects for a config entry."""

    manager: ChannelManager
    client: HikvisionIsapiClient
    task: asyncio.Task | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


async def _run_stream(runtime: RuntimeData) -> None:
    """Maintain long-lived alertStream connection with reconnects."""
    entry = runtime.manager.entry
    reconnect_delay = int(
        entry.data.get(CONF_RECONNECT_DELAY_SECONDS, DEFAULT_RECONNECT_DELAY_SECONDS)
    )
    reconnect_delay = max(1, reconnect_delay)
    backoff = reconnect_delay

    async def _on_event(event: dict) -> None:
        runtime.manager.process_event(event)

    while not runtime.stop_event.is_set():
        try:
            await runtime.client.stream_alerts(_on_event, runtime.stop_event)
            backoff = reconnect_delay
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("alertStream connection failed: %s", err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up via YAML (not used)."""
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries and channel override format."""
    if config_entry.version > 2:
        return False

    if config_entry.version == 1:
        overrides = _parse_overrides(
            config_entry.data.get(CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES, "")
        )
        timeout_store = HikvisionChannelTimeoutStore(hass, config_entry.entry_id)
        existing = await timeout_store.async_load()
        existing.update(overrides)
        await timeout_store.async_save(existing)

        new_data = dict(config_entry.data)
        new_data.pop(CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES, None)
        hass.config_entries.async_update_entry(config_entry, data=new_data, version=2)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hikvision ISAPI Events from UI config entry."""
    session = async_get_clientsession(hass)
    client = HikvisionIsapiClient(
        session=session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        use_ssl=entry.data[CONF_USE_SSL],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    timeout_store = HikvisionChannelTimeoutStore(hass, entry.entry_id)
    stored_timeouts = await timeout_store.async_load()
    if not stored_timeouts and entry.data.get(CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES):
        stored_timeouts = _parse_overrides(entry.data[CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES])
        await timeout_store.async_save(stored_timeouts)

    manager = ChannelManager(hass, entry, timeout_store, stored_timeouts)
    runtime = RuntimeData(manager=manager, client=client)

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={manager.dvr_identifier},
        manufacturer="Hikvision",
        model="ISAPI Event Source",
        name=f"Hikvision DVR {entry.data[CONF_HOST]}",
    )

    channels = await discover_channels(client)
    manager.add_discovered_channels(channels)

    runtime.task = hass.async_create_task(_run_stream(runtime))

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_RUNTIME: runtime}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime: RuntimeData = hass.data[DOMAIN][entry.entry_id][DATA_RUNTIME]
    runtime.stop_event.set()
    runtime.manager.shutdown()
    if runtime.task:
        runtime.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.task

    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True
