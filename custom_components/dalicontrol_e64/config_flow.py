"""Config flow: HTTP (gateway IP) or KNX (gateway individual address)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from dalicontrol_e64 import HttpClient
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback

from .const import (CONF_CHANNELS, CONF_KNX_ADDRESS, CONF_RGBW, CONF_TRANSPORT, DEFAULT_CHANNELS,
                    DOMAIN, TRANSPORT_HTTP, TRANSPORT_KNX)
from .gateway import parse_channels, parse_strips


def _check_lights(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    try:
        channels = parse_channels(user_input[CONF_CHANNELS])
    except ValueError:
        errors[CONF_CHANNELS] = "invalid_channels"
        return
    try:
        parse_strips(user_input.get(CONF_RGBW, ""), channels)
    except ValueError:
        errors[CONF_RGBW] = "invalid_strips"


def _lights(user_input: dict[str, Any]) -> dict[str, str]:
    return {CONF_CHANNELS: user_input[CONF_CHANNELS], CONF_RGBW: user_input.get(CONF_RGBW, "")}


def _valid_individual_address(text: str) -> bool:
    parts = text.split(".")
    try:
        area, line, device = (int(p) for p in parts)
    except ValueError:
        return False
    return len(parts) == 3 and 0 <= area <= 15 and 0 <= line <= 15 and 0 <= device <= 255


class DaliControlConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[TRANSPORT_HTTP, TRANSPORT_KNX])

    async def async_step_http(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(f"http:{host}")
            self._abort_if_unique_id_configured()
            _check_lights(user_input, errors)
            # Read-only check (general failure flags). Never a write.
            if not errors and not await self.hass.async_add_executor_job(
                    HttpClient(host, timeout=10).reachable):
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_create_entry(
                    title=f"DaliControl e64 {host}",
                    data={CONF_TRANSPORT: TRANSPORT_HTTP, CONF_HOST: host, **_lights(user_input)})
        return self.async_show_form(step_id="http", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_CHANNELS, default=DEFAULT_CHANNELS): str,
            vol.Optional(CONF_RGBW, default=""): str,
        }))

    async def async_step_knx(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if "knx" not in self.hass.config.components:
            return self.async_abort(reason="knx_not_set_up")
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_KNX_ADDRESS].strip()
            await self.async_set_unique_id(f"knx:{address}")
            self._abort_if_unique_id_configured()
            if not _valid_individual_address(address):
                errors[CONF_KNX_ADDRESS] = "invalid_address"
            _check_lights(user_input, errors)
            if not errors:
                # Checked on the first command: the interface object type must be C352.
                return self.async_create_entry(
                    title=f"DaliControl e64 {address}",
                    data={CONF_TRANSPORT: TRANSPORT_KNX, CONF_KNX_ADDRESS: address,
                          **_lights(user_input)})
        return self.async_show_form(step_id="knx", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_KNX_ADDRESS): str,
            vol.Required(CONF_CHANNELS, default=DEFAULT_CHANNELS): str,
            vol.Optional(CONF_RGBW, default=""): str,
        }))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DaliControlOptionsFlow()


class DaliControlOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            _check_lights(user_input, errors)
            if not errors:
                return self.async_create_entry(data=_lights(user_input))
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_CHANNELS, default=current[CONF_CHANNELS]): str,
            vol.Optional(CONF_RGBW, default=current.get(CONF_RGBW, "")): str,
        }))
