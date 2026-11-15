"""Home Assistant tests against a mocked HTTP client. No hardware involved."""
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dalicontrol_e64.const import DOMAIN
from custom_components.dalicontrol_e64.gateway import parse_channels, parse_strips
from dalicontrol_e64 import commands


class FakeGateway:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, client, command):
        commands.require_operational(command)
        if self.fail:
            raise OSError("timed out")
        self.sent.append(command)


@pytest.fixture
def device():
    fake = FakeGateway()
    with patch("dalicontrol_e64.http.HttpClient.send", autospec=True, side_effect=fake.send), \
            patch("dalicontrol_e64.http.HttpClient.reachable", return_value=True):
        yield fake


async def _setup(hass, channels="1-3", strips=""):
    entry = MockConfigEntry(domain=DOMAIN, title="e64", unique_id="http:192.0.2.10",
                            data={"transport": "http", "host": "192.0.2.10", "channels": channels,
                                  "rgbw_strips": strips})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_parse_channels():
    assert parse_channels("1-3, 7,10-11") == [1, 2, 3, 7, 10, 11]
    for bad in ("", "0", "65", "3-1", "a"):
        with pytest.raises(ValueError):
            parse_channels(bad)


async def test_http_flow(hass, device):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "http"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.10", "channels": "1-99"})
    assert result["errors"] == {"channels": "invalid_channels"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.10", "channels": "1-4"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"transport": "http", "host": "192.0.2.10", "channels": "1-4", "rgbw_strips": ""}
    assert device.sent == []  # setup never writes


async def test_knx_flow_needs_knx_integration(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "knx"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "knx_not_set_up"


async def test_lights(hass, device):
    await _setup(hass)
    assert hass.states.get("light.e64_channel_1").state == "unknown"
    assert device.sent == []

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.e64_channel_1"}, blocking=True)
    await hass.services.async_call("light", "turn_on",
                                   {"entity_id": "light.e64_channel_2", "brightness": 128}, blocking=True)
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.e64_channel_3"}, blocking=True)
    assert device.sent == ["0B00FF", "0B0180", "0B0200"]
    assert hass.states.get("light.e64_channel_2").attributes["brightness"] == 128
    assert hass.states.get("light.e64_channel_3").state == "off"

    await hass.services.async_call("light", "turn_off", {"entity_id": "light.e64_channel_2"}, blocking=True)
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.e64_channel_2"}, blocking=True)
    assert device.sent[-1] == "0B0180"  # back to the last brightness


async def test_broadcasts_and_scene(hass, device):
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "all_on", {}, blocking=True)
    assert device.sent == ["0BFFFF"]
    assert hass.states.get("light.e64_channel_3").attributes["brightness"] == 255

    await hass.services.async_call(DOMAIN, "all_off", {}, blocking=True)
    assert hass.states.get("light.e64_channel_1").state == "off"

    response = await hass.services.async_call(DOMAIN, "recall_scene", {"scene": 5},
                                              blocking=True, return_response=True)
    assert device.sent == ["0BFFFF", "0A0000", "070500"]
    assert response["status"] == "completed"
    assert hass.states.get("light.e64_channel_1").state == "unknown"  # only members move

    await hass.services.async_call(DOMAIN, "set_levels", {"levels": {"1": 10, "3": 0}}, blocking=True)
    assert device.sent[-2:] == ["0B000A", "0B0200"]
    assert hass.states.get("light.e64_channel_1").attributes["brightness"] == 10


async def test_set_levels_rejects_unconfigured_channel(hass, device):
    await _setup(hass)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "set_levels", {"levels": {"9": 10}}, blocking=True)
    assert device.sent == []


async def test_failed_write_raises_and_keeps_state(hass, device):
    await _setup(hass)
    device.fail = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("light", "turn_on", {"entity_id": "light.e64_channel_1"}, blocking=True)
    assert hass.states.get("light.e64_channel_1").state == "unknown"
    device.fail = False
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.e64_channel_1"}, blocking=True)
    assert hass.states.get("light.e64_channel_1").state == "on"


async def test_unload(hass, device):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_parse_strips():
    assert parse_strips("", [1, 2]) == []
    assert parse_strips("20, 14", list(range(1, 30))) == [14, 20]
    for bad in ("7", "14, 16", "x"):
        with pytest.raises(ValueError):
            parse_strips(bad, list(range(1, 10)) + list(range(14, 20)))


async def test_rgbw_strip(hass, device):
    await _setup(hass, channels="1-6", strips="2")
    assert hass.states.get("light.e64_channel_2") is None  # part of the strip
    assert hass.states.get("light.e64_channel_1") is not None
    assert hass.states.get("light.e64_channel_6") is not None
    strip = "light.e64_strip_2_5"
    assert hass.states.get(strip).state == "unknown"

    await hass.services.async_call("light", "turn_on", {"entity_id": strip}, blocking=True)
    assert device.sent == ["0B0100", "0B0200", "0B0300", "0B04FF"]  # white by default
    device.sent.clear()

    await hass.services.async_call("light", "turn_on",
                                   {"entity_id": strip, "rgbw_color": (255, 0, 0, 0), "brightness": 128},
                                   blocking=True)
    assert device.sent == ["0B0180", "0B0200", "0B0300", "0B0400"]
    state = hass.states.get(strip)
    assert state.attributes["rgbw_color"] == (255, 0, 0, 0)
    assert state.attributes["brightness"] == 128
    device.sent.clear()

    # The colour wheel sends hue/saturation; Home Assistant converts it for RGBW lights.
    await hass.services.async_call("light", "turn_on",
                                   {"entity_id": strip, "hs_color": (120, 100)}, blocking=True)
    assert device.sent == ["0B0100", "0B0280", "0B0300", "0B0400"]  # green, brightness kept
    device.sent.clear()

    await hass.services.async_call("light", "turn_on",
                                   {"entity_id": strip, "brightness": 255}, blocking=True)
    assert device.sent == ["0B0100", "0B02FF", "0B0300", "0B0400"]
    device.sent.clear()

    await hass.services.async_call("light", "turn_off", {"entity_id": strip}, blocking=True)
    assert device.sent == ["0B0100", "0B0200", "0B0300", "0B0400"]
    assert hass.states.get(strip).state == "off"

    await hass.services.async_call("light", "turn_on", {"entity_id": strip}, blocking=True)
    assert device.sent[-4:] == ["0B0100", "0B02FF", "0B0300", "0B0400"]  # last colour back

    await hass.services.async_call(DOMAIN, "all_off", {}, blocking=True)
    assert hass.states.get(strip).state == "off"


async def test_flow_rejects_bad_strip(hass, device):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "http"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.10", "channels": "1-5", "rgbw_strips": "3"})
    assert result["errors"] == {"rgbw_strips": "invalid_strips"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.10", "channels": "1-5", "rgbw_strips": "2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["rgbw_strips"] == "2"
