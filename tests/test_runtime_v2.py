"""Real pinned HA: hierarchy, native recipe editing, strict actions and recovery."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.components.light import (
    DATA_COMPONENT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.jai_lighting.manager import Manager
from custom_components.jai_lighting.models import ModelError
from custom_components.jai_lighting.presets import Preset, Target
from custom_components.jai_lighting.storage import Repository


class Device(LightEntity):
    _attr_should_poll = False

    def __init__(self, id_, modes, *, on=True):
        self.entity_id = f"light.fake_{id_}"
        self._attr_unique_id = f"fake-{id_}"
        self._attr_name = f"Fake {id_}"
        self._attr_supported_color_modes = set(modes)
        self._attr_color_mode = next(iter(modes))
        self._attr_is_on = on
        self._attr_brightness = 200 if ColorMode.ONOFF not in modes else None
        self._attr_hs_color = (120, 50)
        self._attr_min_color_temp_kelvin = 2500
        self._attr_max_color_temp_kelvin = 6500
        self._attr_color_temp_kelvin = 3000
        self._attr_effect_list = ["pulse", "off"]
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._attr_effect = "off"
        self.calls = []
        self.failure = False

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", dict(kwargs), self._context))
        if self.failure:
            raise HomeAssistantError("Fake device failure")
        self._attr_is_on = True
        for key, value in kwargs.items():
            setattr(self, f"_attr_{key}", value)
        if "hs_color" in kwargs:
            self._attr_color_mode = ColorMode.HS
        elif "color_temp_kelvin" in kwargs:
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif "rgb_color" in kwargs:
            self._attr_color_mode = ColorMode.RGB
        elif "xy_color" in kwargs:
            self._attr_color_mode = ColorMode.XY
        elif "rgbw_color" in kwargs:
            self._attr_color_mode = ColorMode.RGBW
        elif "rgbww_color" in kwargs:
            self._attr_color_mode = ColorMode.RGBWW
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", dict(kwargs), self._context))
        self._attr_is_on = False
        self.async_write_ha_state()


@pytest.fixture
async def devices(hass):
    result = [
        Device("one", {ColorMode.HS, ColorMode.COLOR_TEMP}),
        Device("two", {ColorMode.BRIGHTNESS}),
        Device("binary", {ColorMode.ONOFF}),
    ]
    await hass.data[DATA_COMPONENT].async_add_entities(result)
    await hass.async_block_till_done()
    return result


async def group(hass, name, members=(), **kwargs):
    flow = await hass.config_entries.flow.async_init(
        "jai_lighting",
        context={"source": "user"},
        data={"name": name, "members": list(members)} | kwargs,
    )
    assert flow["step_id"] == "group_preview", flow
    assert not flow["errors"], flow
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"action": "save"})
    assert result["type"] == "create_entry", result
    assert result["result"].state.value == "loaded"
    return result["result"]


async def ui_preset(hass, name, rows):
    flow = await hass.config_entries.flow.async_init("jai_lighting", context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "preset"}
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"name": name})
    for row in rows:
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"next_step_id": "target"}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], flow["data_schema"](row)
        )
        assert flow["type"] == "menu", flow
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "preset_preview"}
    )
    assert not flow["errors"], flow
    assert "source" in flow["description_placeholders"]["preview"]
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"action": "save"})
    assert result["type"] == "create_entry", result
    assert result["result"].state.value == "loaded"
    return result["result"]


async def action(hass, name, targets, **data):
    return await hass.services.async_call(
        "jai_lighting", name, {"entity_id": targets} | data, blocking=True
    )


async def test_native_hierarchy_shared_faders_metadata_and_noop(hass, devices):
    root = await group(hass, "Root", [d.entity_id for d in devices])
    child = await group(
        hass, "Child", [devices[0].entity_id], parent=root.entry_id, allow_moves=True
    )
    assert all(d.calls == [] for d in devices)  # No startup/configuration commands.
    context = Context()
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_root", "brightness_pct": 50},
        blocking=True,
        context=context,
    )
    assert devices[0].brightness == devices[1].brightness == 100
    assert devices[2].calls == []
    assert len(devices[0].calls) == 1 and devices[0].calls[0][2].id == context.id
    assert child.runtime_data.engine.master == 255
    await action(hass, "set_master", "light.jai_child", brightness_pct=50)
    assert devices[0].brightness == 50
    assert root.runtime_data.engine.master == 128
    state = hass.states.get("light.jai_root")
    assert state.attributes["child_group_ids"] == [child.entry_id]
    assert state.attributes["descendant_leaf_count"] == 3
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.jai_child"}, blocking=True
    )
    assert len(devices[0].calls) == 2  # Already ON RESTORE is a successful no-op.
    count = len(devices[0].calls)
    with pytest.raises(HomeAssistantError, match="Overlapping"):
        await action(hass, "set_master", ["light.jai_root", "light.jai_child"], brightness_pct=25)
    assert len(devices[0].calls) == count
    result = await hass.services.async_call(
        "jai_lighting", "get_topology", {}, blocking=True, return_response=True
    )
    assert set(result["groups"]) == {root.entry_id, child.entry_id}
    assert result["metadata_version"] == 1


async def test_scene_ui_inheritance_partial_caps_and_relative_after_preset(hass, devices):
    root = await group(hass, "Root", [d.entity_id for d in devices])
    child = await group(
        hass, "Child", [devices[0].entity_id], parent=root.entry_id, allow_moves=True
    )
    await action(hass, "set_master", "light.jai_child", brightness_pct=50)
    masters = dict(hass.data["jai_lighting"].snapshot.runtime.masters)
    preset = await ui_preset(
        hass,
        "Recipe",
        [
            {
                "target": f"group:{root.entry_id}",
                "power": "on",
                "assign_brightness": True,
                "brightness_pct": 30,
                "assign_ct": True,
                "color_temp_kelvin": 2700,
            },
            {"target": f"group:{child.entry_id}", "assign_brightness": True, "brightness_pct": 80},
        ],
    )
    for d in devices:
        d.calls.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.jai_recipe"}, blocking=True
    )
    assert [len(d.calls) for d in devices] == [1, 1, 1]
    assert devices[0].brightness == 204 and devices[0].color_temp_kelvin == 2700
    assert devices[1].brightness == 77
    assert devices[2].calls[0][1] == {}
    assert hass.data["jai_lighting"].snapshot.runtime.masters == masters
    assert preset.entry_id in hass.data["jai_lighting"].snapshot.presets
    await action(hass, "set_master", "light.jai_child", brightness_pct=25)
    assert devices[0].brightness == 102
    assert child.runtime_data.engine.master == 64


async def test_strict_control_preserves_properties_and_off_power(hass, devices):
    await group(hass, "Root", [d.entity_id for d in devices])
    await action(hass, "control", "light.jai_root", properties={"color_temp_kelvin": 2200})
    assert devices[0].calls[-1][1] == {"color_temp_kelvin": 2500}
    assert devices[1].calls == devices[2].calls == []
    assert devices[0].brightness == 200
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.jai_root"}, blocking=True
    )
    counts = [len(d.calls) for d in devices]
    with pytest.raises(HomeAssistantError, match="No available"):
        await action(hass, "control", "light.jai_root", properties={"brightness_pct": 80})
    assert [len(d.calls) for d in devices] == counts
    await action(
        hass, "control", "light.jai_root", properties={"power": "on", "brightness_pct": 80}
    )
    assert devices[0].brightness == devices[1].brightness == 204


async def test_registry_rename_and_deleted_id_does_not_rebind(hass, devices):
    entry = await group(hass, "Root", [devices[0].entity_id])
    manager = hass.data["jai_lighting"]
    leaf_id = manager.snapshot.tree.resolve(entry.entry_id)[0]
    registry = er.async_get(hass)
    physical = registry.async_get(devices[0].entity_id)
    registry.async_update_entity(physical.entity_id, new_entity_id="light.renamed_fake")
    await hass.async_block_till_done()
    assert manager.routes()[leaf_id] == "light.renamed_fake"
    await action(hass, "set_master", "light.jai_root", brightness_pct=50)
    assert devices[0].brightness == 100
    registry.async_remove("light.renamed_fake")
    hass.states.async_set("light.renamed_fake", "on", {"brightness": 200})
    await hass.async_block_till_done()
    assert manager.routes()[leaf_id] is None
    count = len(devices[0].calls)
    await action(hass, "set_master", "light.jai_root", brightness_pct=25)
    assert len(devices[0].calls) == count


async def test_partial_failure_scene_action_response_and_no_rollback(hass, devices):
    root = await group(hass, "Root", [d.entity_id for d in devices[:2]])
    await ui_preset(
        hass,
        "Recipe",
        [
            {
                "target": f"group:{root.entry_id}",
                "power": "on",
                "assign_brightness": True,
                "brightness_pct": 50,
            }
        ],
    )
    devices[1].failure = True
    result = await hass.services.async_call(
        "jai_lighting",
        "activate_preset",
        {"entity_id": "scene.jai_recipe"},
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "partial"
    assert devices[0].brightness == 128
    assert len(devices[0].calls) == 1 and len(devices[1].calls) == 1
    assert devices[1].entity_id in result["failed"]
    with pytest.raises(HomeAssistantError, match=devices[1].entity_id):
        await hass.services.async_call(
            "scene", "turn_on", {"entity_id": "scene.jai_recipe"}, blocking=True
        )
    assert hass.states.get("scene.jai_recipe").state not in {"unknown", "unavailable"}


async def test_invalid_reparent_preview_blocks_save_without_hardware(hass, devices):
    root = await group(hass, "Root", [devices[0].entity_id])
    other = await group(hass, "Other", [])
    manager = hass.data["jai_lighting"]
    preset = Preset(
        "test",
        "Draft",
        (
            Target("group", other.entry_id, {"power": "off"}),
            Target("group", root.entry_id, {"brightness_pct": 50}),
        ),
    )
    await manager.repo.save(replace(manager.snapshot, presets={preset.id: preset}))
    flow = await hass.config_entries.options.async_init(root.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"name": "Root", "parent": other.entry_id, "members": [devices[0].entity_id]},
    )
    assert flow["errors"] == {"base": "invalid_preset"}
    assert "sources=" in flow["description_placeholders"]["preview"]
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"action": "save"})
    assert flow["errors"] == {"base": "invalid_preset"}
    assert manager.snapshot.tree.groups[root.entry_id].parent is None
    assert not devices[0].calls


async def test_creation_draft_without_host_aborts_unpublished_state(hass):
    manager = Manager(hass)
    await manager.load()
    draft = manager.group_candidate("token", {"name": "Draft", "members": []})
    await manager.prepare_creation(draft, "token", 0)
    repo = Repository(hass)
    await repo.load()
    assert repo.journal and not repo.snapshot.tree.groups
    await manager.recover()
    assert not manager.repo.journal and not manager.snapshot.tree.groups


async def test_edit_interruption_recovers_after_host_update(hass, devices):
    root = await group(hass, "Root", [devices[0].entity_id])
    manager = hass.data["jai_lighting"]
    original_identity = er.async_get(hass).async_get("light.jai_root").id
    candidate = manager.group_candidate(
        root.entry_id, {"name": "Renamed", "members": [devices[0].entity_id]}
    )

    async def interrupted(hosts):
        raise ModelError("interrupted before host durability confirmation")

    with patch.object(manager, "_host_durable", interrupted):
        with pytest.raises(ModelError, match="interrupted"):
            await manager.commit(
                candidate,
                [{"id": root.entry_id, "kind": "group"}],
                manager.snapshot.config_revision,
            )
    assert manager.repo.journal and manager.snapshot.tree.groups[root.entry_id].name == "Root"
    await manager.recover()
    assert manager.snapshot.tree.groups[root.entry_id].name == "Renamed"
    assert er.async_get(hass).async_get("light.jai_root").id == original_identity
    assert not devices[0].calls


async def test_storage_failure_pre_dispatch_and_all_unavailable(hass, devices, monkeypatch):
    await group(hass, "Root", [devices[0].entity_id])
    manager = hass.data["jai_lighting"]

    async def fail(data):
        raise OSError("disk full")

    original = manager.repo.store.async_save
    monkeypatch.setattr(manager.repo.store, "async_save", fail)
    with pytest.raises(HomeAssistantError, match="disk full"):
        await action(hass, "set_master", "light.jai_root", brightness_pct=50)
    assert not devices[0].calls
    assert manager.snapshot.runtime.masters[manager.snapshot.tree.children[None][0]] == 255
    monkeypatch.setattr(manager.repo.store, "async_save", original)
    devices[0]._attr_available = False
    devices[0].async_write_ha_state()
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="No available"):
        await action(hass, "control", "light.jai_root", properties={"power": "on"})
    await action(hass, "set_master", "light.jai_root", brightness_pct=50)
    assert not devices[0].calls


async def test_full_bare_on_explicit_brightness_override(hass, devices):
    await group(hass, "Root", [devices[0].entity_id], power_policy="FULL")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.jai_root"}, blocking=True
    )
    assert devices[0].brightness == 255
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.jai_root", "brightness": 128}, blocking=True
    )
    assert devices[0].brightness == 128


async def test_coherent_batch_distinct_properties_and_conflict_rejection(hass, devices):
    root = await group(hass, "Root", [d.entity_id for d in devices[:2]])
    child = await group(
        hass, "Child", [devices[0].entity_id], parent=root.entry_id, allow_moves=True
    )
    await hass.services.async_call(
        "jai_lighting",
        "control",
        {
            "assignments": [
                {"entity_id": "light.jai_root", "properties": {"brightness_pct": 50}},
                {"entity_id": "light.jai_child", "properties": {"color_temp_kelvin": 2700}},
            ]
        },
        blocking=True,
    )
    assert devices[0].calls[-1][1] == {"brightness": 128, "color_temp_kelvin": 2700}
    assert devices[1].calls[-1][1] == {"brightness": 128}
    assert len(devices[0].calls) == len(devices[1].calls) == 1
    with pytest.raises(HomeAssistantError, match="conflict|Conflict"):
        await hass.services.async_call(
            "jai_lighting",
            "control",
            {
                "assignments": [
                    {"entity_id": "light.jai_root", "properties": {"brightness_pct": 50}},
                    {"entity_id": "light.jai_child", "properties": {"brightness_pct": 80}},
                ]
            },
            blocking=True,
        )
    assert len(devices[0].calls) == len(devices[1].calls) == 1
    assert child.runtime_data.engine.master == 255


@pytest.mark.parametrize(
    "mode", [ColorMode.HS, ColorMode.RGB, ColorMode.XY, ColorMode.RGBW, ColorMode.RGBWW]
)
async def test_native_color_conversion_preserves_brightness_and_temperature_kind(hass, mode):
    modes = {mode, ColorMode.COLOR_TEMP} if mode == ColorMode.RGBWW else {mode}
    device = Device("color", modes)
    await hass.data[DATA_COMPONENT].async_add_entities([device])
    await group(hass, "Color", [device.entity_id])
    await action(
        hass,
        "control",
        "light.jai_color",
        properties={"color": {"kind": "rgb", "value": [64, 32, 16]}},
    )
    assert device.brightness == 200
    assert "brightness" not in device.calls[-1][1]
    assert list(device.calls[-1][1]) == [f"{mode.value}_color"]
    if mode == ColorMode.RGB:
        assert device.calls[-1][1]["rgb_color"] == (255, 128, 64)
    count = len(device.calls)
    if mode == ColorMode.RGBWW:
        await action(hass, "control", "light.jai_color", properties={"color_temp_kelvin": 2700})
        assert device.calls[-1][1] == {"color_temp_kelvin": 2700}
    else:
        with pytest.raises(HomeAssistantError, match="No available"):
            await action(hass, "control", "light.jai_color", properties={"color_temp_kelvin": 2700})
        assert len(device.calls) == count


async def test_effect_subset_and_invalid_semantics_precede_dispatch(hass, devices):
    devices[1]._attr_effect_list = ["off"]
    devices[1].async_write_ha_state()
    await group(hass, "Root", [d.entity_id for d in devices[:2]])
    await action(hass, "control", "light.jai_root", properties={"effect": "pulse"})
    assert devices[0].calls[-1][1] == {"effect": "pulse"}
    assert devices[1].calls == []
    with pytest.raises(HomeAssistantError, match="mutually exclusive"):
        await action(
            hass,
            "control",
            "light.jai_root",
            properties={"color_temp_kelvin": 2700, "color": {"kind": "hs", "value": [120, 50]}},
        )
    with pytest.raises(HomeAssistantError, match="Power"):
        await action(hass, "control", "light.jai_root", properties={"power": []})
    assert len(devices[0].calls) == 1


async def test_nonempty_deletion_preserves_blocked_topology_and_empty_deletion_journal(
    hass, devices
):
    root = await group(hass, "Root", [devices[0].entity_id])
    child = await group(hass, "Child", [], parent=root.entry_id)
    manager = hass.data["jai_lighting"]
    leaf = manager.snapshot.tree.resolve(root.entry_id)[0]
    await hass.config_entries.async_remove(root.entry_id)
    await hass.async_block_till_done()
    assert root.entry_id in manager.snapshot.tree.groups
    assert not manager.observations()[leaf].available
    assert manager.observations()[leaf].reason == "branch_disabled"
    assert not devices[0].calls
    await hass.config_entries.async_remove(child.entry_id)
    await hass.async_block_till_done()
    assert child.entry_id not in manager.snapshot.tree.groups
    assert not manager.repo.journal
    repaired = await group(
        hass, "Repaired", [devices[0].entity_id], allow_moves=True, remove_orphans=[root.entry_id]
    )
    assert root.entry_id not in manager.snapshot.tree.groups
    assert manager.snapshot.tree.resolve(repaired.entry_id) == (leaf,)
    assert manager.observations()[leaf].available
    assert not devices[0].calls


async def test_removal_interruption_recovers_from_durable_journal(hass):
    root = await group(hass, "Empty")
    manager = hass.data["jai_lighting"]
    candidate = replace(
        manager.snapshot,
        tree=manager.snapshot.tree.__class__(),
        runtime=manager.snapshot.runtime.__class__(),
    )
    await manager.repo.prepare(candidate, [{"id": root.entry_id, "removed": True}], kind="remove")
    assert hass.config_entries.async_get_entry(root.entry_id)
    await manager.recover()
    assert hass.config_entries.async_get_entry(root.entry_id) is None
    assert root.entry_id not in manager.snapshot.tree.groups
    assert not manager.repo.journal


async def test_preset_repair_inside_topology_draft(hass, devices):
    root = await group(hass, "Root", [devices[0].entity_id])
    other = await group(hass, "Other")
    preset = await ui_preset(
        hass,
        "Recipe",
        [
            {"target": f"group:{other.entry_id}", "power": "off"},
            {"target": f"group:{root.entry_id}", "assign_brightness": True, "brightness_pct": 50},
        ],
    )
    manager = hass.data["jai_lighting"]
    flow = await hass.config_entries.options.async_init(root.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"name": "Root", "parent": other.entry_id, "members": [devices[0].entity_id]},
    )
    assert flow["errors"] == {"base": "invalid_preset"}
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"action": "repair_preset"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"preset": preset.entry_id}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "edit_target"}
    )
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"row": "0"})
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], flow["data_schema"]({"target": f"group:{other.entry_id}", "power": "on"})
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "preset_preview"}
    )
    assert not flow["errors"]
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"action": "save"})
    assert flow["step_id"] == "group_preview" and not flow["errors"]
    assert manager.snapshot.tree.groups[root.entry_id].parent is None
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"action": "save"})
    assert flow["type"] == "create_entry"
    assert manager.snapshot.tree.groups[root.entry_id].parent == other.entry_id
    assert manager.snapshot.presets[preset.entry_id].targets[0].properties["power"] == "on"
    assert not devices[0].calls


async def test_legacy_overlap_owner_flow_preserves_identity_and_memory_without_commands(
    hass, devices
):
    from conftest import make_entry
    from homeassistant.core import State
    from homeassistant.helpers.restore_state import RestoredExtraData, StoredState, async_get
    from homeassistant.util import dt

    devices[0]._attr_is_on = False
    devices[0].async_write_ha_state()
    first = make_entry([devices[0].entity_id], "Legacy one")
    second = make_entry([devices[0].entity_id], "Legacy two")
    for entry, master, target in [(first, 90, 40), (second, 180, 80)]:
        # Seed a pre-existing installation before invoking the real migration API.
        hass.config_entries._entries[entry.entry_id] = entry
        virtual = er.async_get(hass).async_get_or_create(
            "light",
            "jai_lighting",
            entry.entry_id,
            config_entry=entry,
            suggested_object_id=f"legacy_{master}",
        )
        async_get(hass).last_states[virtual.entity_id] = StoredState(
            State(virtual.entity_id, "off"),
            RestoredExtraData(
                {"version": 1, "master": master, "memory": {devices[0].entity_id: target}}
            ),
            dt.utcnow(),
        )
    original_registry = er.async_get(hass).async_get_entity_id(
        "light", "jai_lighting", second.entry_id
    )
    assert not await hass.config_entries.async_setup(first.entry_id)
    assert first.version == second.version == 1
    manager = hass.data["jai_lighting"]
    assert not manager.snapshot.tree.groups
    flow = await hass.config_entries.options.async_init(first.entry_id)
    assert flow["step_id"] == "migration"
    assert (
        "40" in flow["description_placeholders"]["preview"]
        and "80" in flow["description_placeholders"]["preview"]
    )
    key = next(iter(flow["data_schema"].schema)).schema
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {key: second.entry_id}
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert first.version == second.version == 2
    assert first.state.value == second.state.value == "loaded"
    assert manager.snapshot.runtime.masters == {first.entry_id: 90, second.entry_id: 180}
    assert manager.snapshot.tree.resolve(first.entry_id) == ()
    leaf = manager.snapshot.tree.resolve(second.entry_id)[0]
    assert manager.snapshot.runtime.targets[leaf] == 80
    assert (
        er.async_get(hass).async_get_entity_id("light", "jai_lighting", second.entry_id)
        == original_registry
    )
    assert manager.repo.legacy["restore"][second.entry_id]["memory"][devices[0].entity_id] == 80
    assert not devices[0].calls


async def test_migration_prepare_failure_and_post_host_interruption(hass, devices):
    from conftest import make_entry

    legacy = make_entry([devices[0].entity_id], "Legacy")
    hass.config_entries._entries[legacy.entry_id] = legacy
    manager = Manager(hass)
    hass.data["jai_lighting"] = manager
    await manager.load()

    async def disk_failure(data):
        raise OSError("prepare failed")

    with patch.object(manager.repo.store, "async_save", disk_failure):
        with pytest.raises(OSError, match="prepare"):
            await manager.migrate()
    assert legacy.version == 1 and "members" in legacy.data
    assert not manager.snapshot.tree.groups and not manager.repo.journal

    async def interruption(hosts):
        raise ModelError("interrupted")

    with patch.object(manager, "_host_durable", interruption):
        with pytest.raises(ModelError, match="interrupted"):
            await manager.migrate()
    assert legacy.version == 2 and manager.repo.journal
    assert not manager.snapshot.tree.groups
    assert manager.repo.legacy["entries"][0]["data"]["members"] == [devices[0].entity_id]
    await manager.recover()
    assert legacy.entry_id in manager.snapshot.tree.groups
    assert not manager.repo.journal and not devices[0].calls


async def test_rgbww_missing_public_conversion_bounds_skips_safely(hass):
    device = Device("color", {ColorMode.RGBWW})
    await hass.data[DATA_COMPONENT].async_add_entities([device])
    await group(hass, "Color", [device.entity_id])
    with pytest.raises(HomeAssistantError, match="No available"):
        await action(
            hass,
            "control",
            "light.jai_color",
            properties={"color": {"kind": "hs", "value": [120, 50]}},
        )
    assert not device.calls
    assert "color:missing_conversion_bounds" in next(
        iter(hass.data["jai_lighting"].outcome["skipped"].values())
    )


async def test_concurrent_ancestor_child_serialization_and_cancelled_dispatch_cleanup(
    hass, devices
):
    import asyncio

    root = await group(hass, "Root", [devices[0].entity_id])
    child = await group(
        hass, "Child", [devices[0].entity_id], parent=root.entry_id, allow_moves=True
    )
    started, release = asyncio.Event(), asyncio.Event()
    original = devices[0].async_turn_on

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        await original(**kwargs)

    with patch.object(devices[0], "async_turn_on", delayed):
        first = asyncio.create_task(action(hass, "set_master", "light.jai_root", brightness_pct=50))
        await started.wait()
        second = asyncio.create_task(
            action(hass, "set_master", "light.jai_child", brightness_pct=50)
        )
        await asyncio.sleep(0)
        assert not devices[0].calls
        release.set()
        await asyncio.gather(first, second)
    assert [c[1]["brightness"] for c in devices[0].calls] == [100, 50]
    assert root.runtime_data.engine.master == child.runtime_data.engine.master == 128
    started.clear()
    release.clear()
    manager = hass.data["jai_lighting"]
    with patch.object(devices[0], "async_turn_on", delayed):
        pending = asyncio.create_task(
            manager.execute("master", Context(), groups=[root.entry_id], master=64)
        )
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert manager.outcome["status"] == "cancelled_uncertain"
    assert not manager.lock.locked()
    assert root.runtime_data.engine.master == 64
    count = len(devices[0].calls)
    await action(hass, "set_master", "light.jai_root", brightness_pct=25)
    assert len(devices[0].calls) == count  # Same accepted position: never retry.


async def test_post_dispatch_storage_failure_preserves_real_baseline(hass, devices):
    await group(hass, "Root", [devices[0].entity_id])
    manager = hass.data["jai_lighting"]
    original = manager.repo.store.async_save
    saves = 0

    async def fail_third(data):
        nonlocal saves
        saves += 1
        if saves >= 3:
            raise OSError("observation save failed")
        await original(data)

    with patch.object(manager.repo.store, "async_save", fail_third):
        with pytest.raises(HomeAssistantError, match="Devices may have changed"):
            await action(hass, "set_master", "light.jai_root", brightness_pct=50)
        assert devices[0].brightness == 100
        assert manager.outcome["status"] == "storage_failed_after_dispatch"
        with pytest.raises(HomeAssistantError, match="observation save failed"):
            await action(hass, "set_master", "light.jai_root", brightness_pct=25)
        assert len(devices[0].calls) == 1
    await action(hass, "set_master", "light.jai_root", brightness_pct=25)
    assert devices[0].brightness == 50
    assert not manager.repo.failed


async def test_hierarchy_store_restart_independent_masters_and_no_startup_commands(
    hass, devices, tmp_path
):
    from conftest import create_hass

    root = await group(hass, "Root", [devices[0].entity_id])
    child = await group(
        hass, "Child", [devices[0].entity_id], parent=root.entry_id, allow_moves=True
    )
    await action(hass, "set_master", "light.jai_root", brightness_pct=50)
    await action(hass, "set_master", "light.jai_child", brightness_pct=25)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.jai_root"}, blocking=True
    )
    manager = hass.data["jai_lighting"]
    retained = manager.snapshot.runtime.copy()
    await hass.async_stop(force=True)
    restarted = await create_hass(tmp_path)
    try:
        device = Device("one", {ColorMode.HS, ColorMode.COLOR_TEMP}, on=False)
        await restarted.data[DATA_COMPONENT].async_add_entities([device])
        assert await restarted.config_entries.async_setup(root.entry_id)
        await restarted.async_block_till_done()
        assert restarted.config_entries.async_get_entry(child.entry_id).state.value == "loaded"
        assert restarted.data["jai_lighting"].snapshot.runtime.masters == retained.masters
        assert restarted.data["jai_lighting"].snapshot.runtime.targets == retained.targets
        assert not device.calls
        await restarted.services.async_call(
            "light", "turn_on", {"entity_id": "light.jai_child"}, blocking=True
        )
        assert device.brightness == 25
        assert len(device.calls) == 1
    finally:
        await restarted.async_stop(force=True)


async def test_new_group_creation_remaps_repaired_preset_target_to_assigned_identity(hass, devices):
    root = await group(hass, "Root", [devices[0].entity_id])
    preset = await ui_preset(hass, "Recipe", [{"target": f"group:{root.entry_id}", "power": "on"}])
    flow = await hass.config_entries.flow.async_init(
        "jai_lighting",
        context={"source": "user"},
        data={"name": "New", "members": [devices[0].entity_id], "allow_moves": True},
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"action": "repair_preset"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"preset": preset.entry_id}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "edit_target"}
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"row": "0"})
    target_selector = next(v for k, v in flow["data_schema"].schema.items() if k.schema == "target")
    provisional = next(
        o["value"] for o in target_selector.config["options"] if o["label"].startswith("Group: New")
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], flow["data_schema"]({"target": provisional, "power": "on"})
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "preset_preview"}
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"action": "save"})
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"action": "save"})
    assert result["type"] == "create_entry" and result["result"].state.value == "loaded"
    assert (
        hass.data["jai_lighting"].snapshot.presets[preset.entry_id].targets[0].id
        == result["result"].entry_id
    )
    assert not devices[0].calls
