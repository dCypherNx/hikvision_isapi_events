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
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ATTR_LAST_EVENT_DATETIME,
    ATTR_LAST_EVENT_STATE,
    ATTR_LAST_EVENT_TYPE,
    ATTR_LAST_TARGET_TYPE,
    CONF_DEFAULT_OFF_DELAY_SECONDS,
    CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES,
    CONF_RECONNECT_DELAY_SECONDS,
    CONF_USE_SSL,
    DATA_RUNTIME,
    DEFAULT_RECONNECT_DELAY_SECONDS,
    DOMAIN,
    EVENT_TYPE_VMD,
    PLATFORMS,
)
from .discovery import discover_channels
from .isapi_client import HikvisionIsapiClient

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


class HikvisionEventHub:
    """Central state/event hub consumed by entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._states: dict[int, ChannelState] = {}
        self._entity_listeners: list[Callable[[int], None]] = []
        self._channel_listeners: list[Callable[[int], None]] = []
        self._off_delays = _parse_overrides(
            entry.data.get(CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES, "")
        )

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

    def _channel_delay(self, channel_id: int) -> int:
        default_delay = int(self.entry.data.get(CONF_DEFAULT_OFF_DELAY_SECONDS, 30))
        return self._off_delays.get(channel_id, default_delay)

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

        delay = self._channel_delay(channel_id)
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
        if not line:
            continue
        if "=" not in line:
            continue
        left, right = line.split("=", 1)
        try:
            channel_id = int(left.strip())
            seconds = int(right.strip())
        except ValueError:
            continue
        overrides[channel_id] = max(0, min(1800, seconds))
    return overrides


@dataclass(slots=True)
class RuntimeData:
    """In-memory runtime objects for a config entry."""

    hub: HikvisionEventHub
    client: HikvisionIsapiClient
    task: asyncio.Task | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


async def _run_stream(runtime: RuntimeData) -> None:
    """Maintain long-lived alertStream connection with reconnects."""
    entry = runtime.hub.entry
    reconnect_delay = int(
        entry.data.get(CONF_RECONNECT_DELAY_SECONDS, DEFAULT_RECONNECT_DELAY_SECONDS)
    )
    reconnect_delay = max(1, reconnect_delay)
    backoff = reconnect_delay

    async def _on_event(event: dict) -> None:
        runtime.hub.process_event(event)

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
    hub = HikvisionEventHub(hass, entry)
    runtime = RuntimeData(hub=hub, client=client)

    channels = await discover_channels(client)
    hub.add_discovered_channels(channels)

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
    runtime.hub.shutdown()
    if runtime.task:
        runtime.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.task

    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True
