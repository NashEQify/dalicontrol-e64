"""Lights: one dimmable light per channel, one RGBW light per configured strip."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (ATTR_BRIGHTNESS, ATTR_RGBW_COLOR, ColorMode,
                                            LightEntity)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .gateway import Gateway


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    gateway: Gateway = entry.runtime_data
    device = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
                        manufacturer="IPAS", model="DaliControl e64")
    in_strips = {c for start in gateway.strips for c in range(start, start + 4)}
    entities: list[LightEntity] = [
        ChannelLight(gateway, entry.entry_id, channel, device)
        for channel in gateway.channels if channel not in in_strips]
    entities += [StripLight(gateway, entry.entry_id, start, device) for start in gateway.strips]
    async_add_entities(entities)


class _GatewayLight(RestoreEntity, LightEntity):
    """State is what was last sent. The gateway gives no reliable feedback."""

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_should_poll = False

    def __init__(self, gateway: Gateway, channels: list[int], device: DeviceInfo):
        self._gateway = gateway
        self._channels = channels
        self._attr_device_info = device

    def _levels(self) -> list[int | None]:
        return [self._gateway.levels.get(c) for c in self._channels]

    def _restore(self, values: list[int]) -> None:
        for channel, value in zip(self._channels, values):
            if self._gateway.levels.get(channel) is None:
                self._gateway.levels[channel] = value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._restore_from(last)
        self._remember()
        self._gateway.listeners.append(self._changed)
        self.async_on_remove(lambda: self._gateway.listeners.remove(self._changed))

    def _changed(self) -> None:
        self._remember()
        self.async_write_ha_state()

    def _restore_from(self, last) -> None:
        raise NotImplementedError

    def _remember(self) -> None:
        raise NotImplementedError

    async def _send(self, values: list[int]) -> None:
        await self._gateway.set_levels(dict(zip(self._channels, values)), label=self.entity_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send([0] * len(self._channels))


class ChannelLight(_GatewayLight):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, gateway: Gateway, entry_id: str, channel: int, device: DeviceInfo):
        super().__init__(gateway, [channel], device)
        self._last_on = 255
        self._attr_name = f"Channel {channel}"
        self._attr_unique_id = f"{entry_id}_channel_{channel}"
        self._attr_extra_state_attributes = {"dali_channel": channel}

    def _restore_from(self, last) -> None:
        if last.state == "off":
            self._restore([0])
        elif last.state == "on":
            self._restore([int(last.attributes.get(ATTR_BRIGHTNESS) or 255)])

    def _remember(self) -> None:
        if level := self._levels()[0]:
            self._last_on = level

    @property
    def is_on(self) -> bool | None:
        level = self._levels()[0]
        return None if level is None else level > 0

    @property
    def brightness(self) -> int | None:
        return self._levels()[0] or None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send([max(1, int(kwargs.get(ATTR_BRIGHTNESS, self._last_on)))])


class StripLight(_GatewayLight):
    """Four consecutive channels R, G, B, W as one colour light.

    Home Assistant's colour wheel, white slider and brightness all map onto
    the four channel levels. Colour and brightness are derived from those
    levels, so a scene recall or all-off keeps the state consistent.
    """

    _attr_color_mode = ColorMode.RGBW
    _attr_supported_color_modes = {ColorMode.RGBW}

    def __init__(self, gateway: Gateway, entry_id: str, start: int, device: DeviceInfo):
        super().__init__(gateway, list(range(start, start + 4)), device)
        self._last_color = (0, 0, 0, 255)
        self._last_brightness = 255
        self._attr_name = f"Strip {start}-{start + 3}"
        self._attr_unique_id = f"{entry_id}_strip_{start}"
        self._attr_extra_state_attributes = {"dali_channels": self._channels}

    @staticmethod
    def _normalise(values) -> tuple[int, int, int, int]:
        top = max(values)
        return tuple(round(v * 255 / top) for v in values) if top else (0, 0, 0, 0)

    def _restore_from(self, last) -> None:
        if last.state == "off":
            self._restore([0, 0, 0, 0])
        elif last.state == "on" and (color := last.attributes.get(ATTR_RGBW_COLOR)):
            bright = int(last.attributes.get(ATTR_BRIGHTNESS) or 255)
            self._restore([round(c * bright / 255) for c in self._normalise(color)])

    def _remember(self) -> None:
        levels = self._levels()
        if None not in levels and max(levels):
            self._last_color = self._normalise(levels)
            self._last_brightness = max(levels)

    @property
    def is_on(self) -> bool | None:
        levels = self._levels()
        return None if None in levels else max(levels) > 0

    @property
    def brightness(self) -> int | None:
        levels = self._levels()
        return None if None in levels or not max(levels) else max(levels)

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        levels = self._levels()
        return None if None in levels or not max(levels) else self._normalise(levels)

    async def async_turn_on(self, **kwargs: Any) -> None:
        color = self._normalise(kwargs.get(ATTR_RGBW_COLOR, self._last_color))
        if not max(color):
            color = (0, 0, 0, 255)
        bright = max(1, int(kwargs.get(ATTR_BRIGHTNESS, self._last_brightness)))
        await self._send([round(c * bright / 255) for c in color])
