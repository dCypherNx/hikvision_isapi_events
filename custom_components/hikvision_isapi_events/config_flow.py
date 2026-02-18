"""Config flow for Hikvision ISAPI Events."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DEFAULT_OFF_DELAY_SECONDS,
    CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES,
    CONF_RECONNECT_DELAY_SECONDS,
    CONF_USE_SSL,
    DEFAULT_OFF_DELAY_SECONDS,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_DELAY_SECONDS,
    DEFAULT_USE_SSL,
    DOMAIN,
    MAX_OFF_DELAY_SECONDS,
    MIN_OFF_DELAY_SECONDS,
)
from .isapi_client import HikvisionIsapiClient


def _validate_overrides(raw: str) -> bool:
    for idx, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"line_{idx}")
        left, right = line.split("=", 1)
        try:
            _channel_id = int(left.strip())
            seconds = int(right.strip())
        except ValueError as err:
            raise ValueError(f"line_{idx}") from err
        if seconds < MIN_OFF_DELAY_SECONDS or seconds > MAX_OFF_DELAY_SECONDS:
            raise ValueError(f"line_{idx}")
    return True


class HikvisionIsapiEventsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hikvision ISAPI Events."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            overrides = user_input.get(CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES, "")
            try:
                _validate_overrides(overrides)
            except ValueError:
                errors[CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES] = "invalid_override_format"

            default_delay = user_input[CONF_DEFAULT_OFF_DELAY_SECONDS]
            if default_delay < MIN_OFF_DELAY_SECONDS or default_delay > MAX_OFF_DELAY_SECONDS:
                errors[CONF_DEFAULT_OFF_DELAY_SECONDS] = "invalid_delay"

            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()

                session = async_get_clientsession(self.hass)
                client = HikvisionIsapiClient(
                    session=session,
                    host=user_input[CONF_HOST],
                    port=user_input[CONF_PORT],
                    use_ssl=user_input[CONF_USE_SSL],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                )
                try:
                    is_valid = await client.validate_device_info()
                except Exception:  # noqa: BLE001
                    is_valid = False

                if not is_valid:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=f"Hikvision {user_input[CONF_HOST]}",
                        data=user_input,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Required(CONF_USE_SSL, default=DEFAULT_USE_SSL): bool,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_DEFAULT_OFF_DELAY_SECONDS,
                        default=DEFAULT_OFF_DELAY_SECONDS,
                    ): vol.All(int, vol.Range(min=MIN_OFF_DELAY_SECONDS, max=MAX_OFF_DELAY_SECONDS)),
                    vol.Optional(CONF_PER_CHANNEL_OFF_DELAY_OVERRIDES, default=""): str,
                    vol.Required(
                        CONF_RECONNECT_DELAY_SECONDS,
                        default=DEFAULT_RECONNECT_DELAY_SECONDS,
                    ): vol.All(int, vol.Range(min=1, max=300)),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """No separate options flow (configure in initial form)."""
        return None
