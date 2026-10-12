"""Prove Bubble's number adapter against the pinned, real HA template platform."""

import pytest
from homeassistant.components.light import DATA_COMPONENT, ColorMode, LightEntity
from homeassistant.components.template.number import NUMBER_CONFIG_ENTRY_SCHEMA, NUMBER_YAML_SCHEMA
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from scripts.bubble_helpers import master_helper_options, master_number


class Lamp(LightEntity):
    """Only simulated hardware: every hardware intention remains observable."""

    _attr_name = "Adapter fixture lamp"
    _attr_should_poll = False
    _attr_is_on = False
    _attr_brightness = 200
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self):
        self.entity_id = "light.adapter_fixture"
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", kwargs))
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", kwargs))
        self._attr_is_on = False
        self.async_write_ha_state()


async def setup_adapter(hass, add_group):
    lamp = Lamp()
    await hass.data[DATA_COMPONENT].async_add_entities([lamp])
    entry = await add_group([lamp.entity_id], "Fixture")
    definition = master_number(entry.entry_id, "light.jai_fixture", "Fixture")
    assert await async_setup_component(hass, "template", {"template": [{"number": [definition]}]})
    # Template subscriptions start with HA, as they do on the installed system.
    await hass.async_start()
    await hass.async_block_till_done()
    numbers = hass.states.async_all("number")
    assert len(numbers) == 1
    return lamp, numbers[0].entity_id


async def test_bubble_number_keeps_off_group_off_and_tracks_external_master(hass, add_group):
    lamp, number = await setup_adapter(hass, add_group)
    # Both integration setup and template setup are hardware-command-free.
    assert lamp.calls == []
    assert hass.states.get("light.jai_fixture").state == "off"
    assert float(hass.states.get(number).state) == 100

    # This is the service Bubble Card 3.4.1 uses for a number slider.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 37}, blocking=True
    )
    await hass.async_block_till_done()
    assert lamp.calls == []
    assert hass.states.get("light.jai_fixture").state == "off"
    assert hass.states.get("light.jai_fixture").attributes[
        "master_brightness_pct"
    ] == pytest.approx(37, abs=0.5)
    assert float(hass.states.get(number).state) == pytest.approx(37, abs=0.5)

    # Changes elsewhere must flow back into the helper; it is not a second master.
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_fixture", "brightness_pct": 62},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert float(hass.states.get(number).state) == pytest.approx(62, abs=0.5)
    assert lamp.calls == []


async def test_bubble_number_preserves_normal_relative_dimming_when_on(hass, add_group):
    lamp, number = await setup_adapter(hass, add_group)
    # Simulate a physical state report, not an automated command to real hardware.
    lamp._attr_is_on = True
    lamp.async_write_ha_state()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 50}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(lamp.calls) == 1
    assert lamp.calls[0][0] == "on"
    assert lamp.brightness == 100
    assert lamp.is_on


async def test_bubble_number_unavailable_when_source_unavailable(hass, add_group):
    lamp, number = await setup_adapter(hass, add_group)
    lamp._attr_available = False
    lamp.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get("light.jai_fixture").state == "unavailable"
    assert hass.states.get(number).state == "unavailable"
    assert lamp.calls == []


@pytest.mark.parametrize("value", [0, 101])
async def test_bubble_number_rejects_invalid_master_range(hass, add_group, value):
    lamp, number = await setup_adapter(hass, add_group)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": value}, blocking=True
        )
    assert lamp.calls == []
    assert float(hass.states.get(number).state) == 100


def test_native_yaml_and_config_flow_schemas_and_stable_identity():
    definition = master_number("stable-id", "light.fixture", "A {{ literal }}")
    NUMBER_YAML_SCHEMA(definition)
    NUMBER_CONFIG_ENTRY_SCHEMA(master_helper_options("stable-id", "light.fixture", "Fixture"))
    renamed = master_number("stable-id", "light.renamed", "Renamed")
    assert renamed["unique_id"] == definition["unique_id"]
    assert (
        master_number("another-id", "light.fixture", "Fixture")["unique_id"]
        != definition["unique_id"]
    )


@pytest.mark.parametrize("entity", ["", "switch.fixture", "light.fixture'", "{{ entity }}"])
def test_invalid_entities_never_become_templates(entity):
    with pytest.raises(ValueError):
        master_number("stable-id", entity, "Fixture")
