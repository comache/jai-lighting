"""Actual HA flows, entity lifecycle, actions and state restoration."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import (
    RestoredExtraData,
    StoredState,
)
from homeassistant.helpers.restore_state import (
    async_get as get_restore_data,
)

from custom_components.jai_lighting.coordinator import GroupCoordinator


def put(hass, entity_id, brightness=100, on=True, mode="brightness"):
    attributes = {"supported_color_modes": [mode]}
    if brightness is not None:
        attributes["brightness"] = brightness
    hass.states.async_set(entity_id, STATE_ON if on else STATE_OFF, attributes)


@pytest.fixture
def physical_services(hass):
    """Emulate real device reports at HA's service boundary, not HA internals."""
    calls = []

    async def handle(call):
        calls.append(call)
        entity_id = call.data["entity_id"]
        old = hass.states.get(entity_id)
        attributes = dict(old.attributes)
        if "brightness" in call.data:
            attributes["brightness"] = call.data["brightness"]
        hass.states.async_set(
            entity_id,
            STATE_ON if call.service == "turn_on" else STATE_OFF,
            attributes,
            context=call.context,
        )

    # Coordinator tests replace physical dispatch only. Entity lifecycle/action
    # tests below use HA's real light service handlers and real LightEntity leaves.
    hass.services.async_register("light", "turn_on", handle)
    hass.services.async_register("light", "turn_off", handle)
    return calls


async def test_setup_and_unload(hass, add_group):
    put(hass, "light.l0", 80)
    entry = await add_group(["light.l0"])
    state = hass.states.get("light.jai_test_group")
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 255
    assert state.attributes["entity_id"] == ["light.l0"]
    assert hass.services.has_service("jai_lighting", "set_master")
    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._unsubscribe is None


async def test_event_updates_manual_baseline_and_availability(hass, add_group):
    put(hass, "light.l0", 80)
    entry = await add_group(["light.l0", "light.missing"])
    put(hass, "light.l0", 150)
    await hass.async_block_till_done()
    assert entry.runtime_data.engine.memory["light.l0"] == 150
    assert entry.runtime_data.engine.master == 255
    hass.states.async_set("light.l0", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("light.jai_test_group").state == "unavailable"
    put(hass, "light.l0", 30, on=False)
    await hass.async_block_till_done()
    state = hass.states.get("light.jai_test_group")
    assert state.state == STATE_OFF
    assert state.attributes["master_brightness"] == 255


async def test_coordinator_round_trip_and_actual_baseline(hass, physical_services):
    put(hass, "light.l0", 100)
    put(hass, "light.l1", 200)
    coordinator = GroupCoordinator(hass, ["light.l0", "light.l1"], asyncio.Lock())
    coordinator.engine.master = 100
    context = Context()
    await coordinator.execute("master", context, 200)
    assert hass.states.get("light.l1").attributes["brightness"] == 255
    await coordinator.execute("master", context, 100)
    assert hass.states.get("light.l1").attributes["brightness"] == 128
    put(hass, "light.l0", 60)
    await coordinator.execute("off", context)
    await coordinator.execute("master", context, 50)
    await coordinator.execute("on", context)
    assert hass.states.get("light.l0").attributes["brightness"] == 30
    assert hass.states.get("light.l1").attributes["brightness"] == 64
    assert all(call.context is context for call in physical_services)


async def test_partial_failure_commits_master_and_reports_failed_leaf(hass, physical_services):
    put(hass, "light.l0", 100)
    put(hass, "light.l1", 100)
    coordinator = GroupCoordinator(hass, ["light.l0", "light.l1"], asyncio.Lock())
    coordinator.engine.master = 100

    async def dispatch(call):
        if call.data["entity_id"] == "light.l1":
            raise HomeAssistantError("Device rejected command")
        put(hass, "light.l0", call.data["brightness"])

    hass.services.async_register("light", "turn_on", dispatch)
    with pytest.raises(HomeAssistantError, match="light.l1"):
        await coordinator.execute("master", Context(), 150)
    assert coordinator.engine.master == 150
    assert hass.states.get("light.l0").attributes["brightness"] == 150
    assert list(coordinator.failures) == ["light.l1"]


async def test_timeout_does_not_invent_member_brightness(hass):
    put(hass, "light.l0", 100)
    hass.services.async_register("light", "turn_on", lambda call: None)
    coordinator = GroupCoordinator(hass, ["light.l0"], asyncio.Lock())
    coordinator.engine.master = 100
    with patch("custom_components.jai_lighting.manager.STATE_TIMEOUT", 0.01):
        with pytest.raises(HomeAssistantError, match="light.l0"):
            await coordinator.execute("master", Context(), 150)
    assert coordinator.engine.memory["light.l0"] == 100
    assert hass.states.get("light.l0").attributes["brightness"] == 100


async def test_delayed_quantized_acknowledgement(hass):
    put(hass, "light.l0", 100)
    reported = asyncio.Event()

    async def report():
        await asyncio.sleep(0.01)
        put(hass, "light.l0", 148)
        reported.set()

    async def handle(call):
        hass.async_create_task(report())

    hass.services.async_register("light", "turn_on", handle)
    coordinator = GroupCoordinator(hass, ["light.l0"], asyncio.Lock())
    coordinator.engine.master = 100
    await coordinator.execute("master", Context(), 150)
    assert reported.is_set()
    assert coordinator.engine.memory["light.l0"] == 148


async def test_config_flow_validation_and_multiple_groups(hass):
    put(hass, "light.l0")
    result = await hass.config_entries.flow.async_init("jai_lighting", context={"source": "user"})
    assert result["type"] == "menu"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "group"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": " ", "members": ["light.l0"]}
    )
    assert result["errors"] == {"base": "invalid_configuration"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Group", "members": ["light.l0", "light.l0"]}
    )
    assert result["errors"] == {"base": "invalid_configuration"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Group", "members": ["light.missing"]}
    )
    assert result["errors"] == {"base": "invalid_configuration"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Test Group", "members": ["light.l0"]}
    )
    assert result["step_id"] == "group_preview"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"action": "save"})
    assert result["type"] == "create_entry"
    first = result["result"]
    assert first.runtime_data.engine.members == ("light.l0",)
    second = await hass.config_entries.flow.async_init(
        "jai_lighting",
        context={"source": "user"},
        data={"name": "Second Group", "members": ["light.l0"]},
    )
    assert second["errors"] == {"base": "invalid_configuration"}
    put(hass, "light.other")
    second = await hass.config_entries.flow.async_configure(
        second["flow_id"], {"name": "Second Group", "members": ["light.other"]}
    )
    second = await hass.config_entries.flow.async_configure(second["flow_id"], {"action": "save"})
    assert second["type"] == "create_entry"
    await hass.async_block_till_done()


async def test_options_replace_member_keeps_entity_id_master_and_memory(hass, add_group):
    put(hass, "light.l0", 80, on=False)
    put(hass, "light.replacement", 45, on=False)
    entry = await add_group(["light.l0"])
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_test_group", "brightness_pct": 50},
        blocking=True,
    )
    master = entry.runtime_data.engine.master
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "New Name", "members": ["light.replacement"]}
    )
    assert result["step_id"] == "group_preview"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"action": "save"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.runtime_data.engine.master == master
    assert entry.runtime_data.engine.memory == {}
    assert hass.states.get("light.jai_test_group").attributes["entity_id"] == ["light.replacement"]
    assert entry.title == "New Name"


async def test_restore_entity_extra_data_when_off(hass, add_group):
    put(hass, "light.l0", 200, on=False)
    restored = hass.states.async_set("light.jai_test_group", STATE_OFF)
    del restored
    from homeassistant.util import dt as dt_util

    get_restore_data(hass).last_states["light.jai_test_group"] = StoredState(
        hass.states.get("light.jai_test_group"),
        RestoredExtraData({"version": 1, "master": 90, "memory": {"light.l0": 60}}),
        dt_util.utcnow(),
    )
    hass.states.async_remove("light.jai_test_group")
    entry = await add_group(["light.l0"])
    assert entry.runtime_data.engine.master == 90
    assert entry.runtime_data.engine.memory["light.l0"] == 60
    assert hass.states.get("light.jai_test_group").attributes["master_brightness"] == 90


async def test_known_nested_groups_rejected_in_flow_and_runtime(hass, add_group):
    put(hass, "light.l0")
    await add_group(["light.l0"])
    result = await hass.config_entries.flow.async_init(
        "jai_lighting",
        context={"source": "user"},
        data={"name": "Parent", "members": ["light.jai_test_group"]},
    )
    assert result["errors"] == {"base": "invalid_configuration"}
    from custom_components.jai_lighting.models import ModelError

    with pytest.raises(ModelError, match="individual light"):
        GroupCoordinator(hass, ["light.jai_test_group"], asyncio.Lock())
    registry = er.async_get(hass)
    registry.async_get_or_create("light", "group", "aggregate", suggested_object_id="aggregate")
    result = await hass.config_entries.flow.async_init(
        "jai_lighting",
        context={"source": "user"},
        data={"name": "Parent", "members": ["light.aggregate"]},
    )
    assert result["errors"] == {"base": "invalid_configuration"}
