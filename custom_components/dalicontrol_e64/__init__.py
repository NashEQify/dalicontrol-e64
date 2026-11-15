"""Home Assistant integration for the IPAS DaliControl e64 KNX/DALI gateway.

Unofficial. Sends only operational commands (levels, scene recalls, all on,
all off); the library refuses everything else. No ETS or DCA is needed.
"""
from __future__ import annotations

import voluptuous as vol
from dalicontrol_e64 import commands
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_CHANNELS, CONF_KNX_ADDRESS, CONF_RGBW, CONF_TRANSPORT, DOMAIN, TRANSPORT_KNX
from .gateway import Gateway, parse_channels, parse_strips

PLATFORMS = [Platform.LIGHT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ATTR_ENTRY = "config_entry_id"
_ENTRY = {vol.Optional(ATTR_ENTRY): cv.string}

SCENE_SCHEMA = vol.Schema({**_ENTRY, vol.Required("scene"): vol.All(vol.Coerce(int), vol.Range(0, 15))})
BROADCAST_SCHEMA = vol.Schema(_ENTRY)
LEVELS_SCHEMA = vol.Schema({
    **_ENTRY,
    vol.Required("levels"): vol.All(
        {vol.All(vol.Coerce(int), vol.Range(1, 64)): vol.All(vol.Coerce(int), vol.Range(0, 255))},
        vol.Length(min=1)),
})


def _gateway(hass: HomeAssistant, call: ServiceCall) -> Gateway:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if len(entries) != 1:
        raise ServiceValidationError(
            "Name the gateway with config_entry_id" if entries else "No DaliControl e64 gateway is loaded")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def recall_scene(call: ServiceCall):
        slot = call.data["scene"]
        return await _gateway(hass, call).broadcast(commands.scene_recall(slot), None, f"scene {slot}")

    async def all_off(call: ServiceCall):
        return await _gateway(hass, call).broadcast(commands.ALL_OFF, 0, "all off")

    async def all_on(call: ServiceCall):
        return await _gateway(hass, call).broadcast(commands.ALL_ON, 255, "all on")

    async def set_levels(call: ServiceCall):
        gateway = _gateway(hass, call)
        levels = call.data["levels"]
        unknown = sorted(set(levels) - set(gateway.channels))
        if unknown:
            raise ServiceValidationError(f"Channels not configured for this gateway: {unknown}")
        return await gateway.set_levels(levels, label="set_levels")

    for name, handler, schema in (
        ("recall_scene", recall_scene, SCENE_SCHEMA),
        ("all_off", all_off, BROADCAST_SCHEMA),
        ("all_on", all_on, BROADCAST_SCHEMA),
        ("set_levels", set_levels, LEVELS_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = {**entry.data, **entry.options}
    transport = data[CONF_TRANSPORT]
    target = data[CONF_KNX_ADDRESS] if transport == TRANSPORT_KNX else data[CONF_HOST]
    channels = parse_channels(data[CONF_CHANNELS])
    entry.runtime_data = Gateway.create(hass, entry.title, transport, target, channels,
                                        parse_strips(data.get(CONF_RGBW, ""), channels))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.writer.close()
    return unloaded
