"""End-to-end actions through real HA LightEntity service handlers."""

import pytest
from homeassistant.components.light import DATA_COMPONENT, ColorMode, LightEntity
from homeassistant.core import Context


class PhysicalLight(LightEntity):
    """Fake hardware behind HA's real entity/platform/action machinery."""

    _attr_should_poll = False

    def __init__(self, entity_id, brightness=200, mode=ColorMode.BRIGHTNESS):
        self.entity_id = entity_id
        self._attr_name = entity_id
        self._attr_is_on = True
        self._attr_brightness = brightness
        self._attr_supported_color_modes = {mode}
        self._attr_color_mode = mode
        self._attr_hs_color = (120, 50) if mode == ColorMode.HS else None
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", kwargs, self._context))
        self._attr_is_on = True
        if "brightness" in kwargs:
            self._attr_brightness = kwargs["brightness"]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", kwargs, self._context))
        self._attr_is_on = False
        self.async_write_ha_state()


@pytest.fixture
async def leaves(hass):
    entities = [
        PhysicalLight("light.dim", 200),
        PhysicalLight("light.color", 120, ColorMode.HS),
        PhysicalLight("light.binary", None, ColorMode.ONOFF),
    ]
    await hass.data[DATA_COMPONENT].async_add_entities(entities)
    await hass.async_block_till_done()
    return entities


async def action(hass, service, **data):
    await hass.services.async_call(
        "light", service, {"entity_id": "light.jai_sigle_led", **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_standard_brightness_and_power_services(hass, add_group, leaves):
    entry = await add_group([leaf.entity_id for leaf in leaves])
    await action(hass, "turn_on", brightness=128)
    assert leaves[0].brightness == 100
    assert leaves[1].brightness == 60
    assert leaves[1].hs_color == (120, 50)
    assert leaves[2].calls == []  # On/off-only hardware is not dimmed.
    await action(hass, "turn_off")
    assert all(not leaf.is_on for leaf in leaves)
    assert entry.runtime_data.engine.master == 128
    await action(hass, "turn_on")
    assert all(leaf.is_on for leaf in leaves)
    assert leaves[0].brightness == 100
    assert leaves[1].brightness == 60
    assert leaves[2].calls[-1][1] == {}
    await action(hass, "turn_on", brightness=0)
    assert all(not leaf.is_on for leaf in leaves)
    assert entry.runtime_data.engine.master == 128


async def test_master_action_while_off_then_normal_on_with_brightness(hass, add_group, leaves):
    entry = await add_group([leaves[0].entity_id])
    await action(hass, "turn_off")
    count = len(leaves[0].calls)
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_sigle_led", "brightness_pct": 50},
        blocking=True,
    )
    assert not leaves[0].is_on
    assert len(leaves[0].calls) == count
    assert entry.runtime_data.engine.memory["light.dim"] == 100
    await action(hass, "turn_on", brightness=64)
    assert leaves[0].is_on
    assert leaves[0].brightness == 50
    assert entry.runtime_data.engine.master == 64


async def test_entity_targeting_context_and_overlapping_groups(hass, add_group, leaves):
    first = await add_group([leaves[0].entity_id])
    second = await add_group([leaves[0].entity_id], "Hol Sus")
    assert first.runtime_data.lock is second.runtime_data.lock
    context = Context()
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_sigle_led", "brightness_pct": 50},
        blocking=True,
        context=context,
    )
    assert leaves[0].calls[-1][2].id == context.id
    assert second.runtime_data.engine.master == 255
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_hol_sus", "brightness_pct": 50},
        blocking=True,
    )
    assert leaves[0].brightness == 50
    assert first.runtime_data.engine.master == 128


@pytest.mark.parametrize("percent", [0, 101, float("nan"), float("inf")])
async def test_master_action_rejects_out_of_range_values(hass, add_group, leaves, percent):
    import probatio

    entry = await add_group([leaves[0].entity_id])
    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            "jai_lighting",
            "set_master",
            {"entity_id": "light.jai_sigle_led", "brightness_pct": percent},
            blocking=True,
        )
    assert entry.runtime_data.engine.master == 255
    assert leaves[0].calls == []
