"""Group form contracts through HA 2026.10.0's flow manager and API serializer."""

import pytest
from homeassistant.components.config.config_entries import ConfigManagerFlowResourceView
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.data_entry_flow import InvalidData

from custom_components.jai_lighting.manager import get_manager


async def group_form(hass):
    flow = await hass.config_entries.flow.async_init("jai_lighting", context={"source": "user"})
    form = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "group"}
    )
    assert form["step_id"] == "group"
    # The same serializer used by /api/config/config_entries/flow/{flow_id}.
    response = ConfigManagerFlowResourceView(hass.config_entries.flow)._prepare_result_json(form)
    return form, {field["name"]: field for field in response["data_schema"]}


async def save_group(hass, form, data):
    preview = await hass.config_entries.flow.async_configure(form["flow_id"], data)
    assert preview["step_id"] == "group_preview", preview
    assert not preview["errors"]
    saved = await hass.config_entries.flow.async_configure(preview["flow_id"], {"action": "save"})
    assert saved["type"] == "create_entry", saved
    await hass.async_block_till_done()
    assert saved["result"].state.value == "loaded"
    return saved["result"]


@pytest.mark.parametrize("with_members", [False, True], ids=["empty-root", "three-lights-root"])
@pytest.mark.parametrize("omit_optional", [False, True], ids=["explicit-empty", "omitted-empty"])
async def test_first_root_group_form(hass, with_members, omit_optional):
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, calls.append)
    members = [f"light.test_{index}" for index in range(3)] if with_members else []
    for entity_id in members:
        hass.states.async_set(entity_id, "on", {"supported_color_modes": ["brightness"]})
    form, fields = await group_form(hass)
    assert fields["parent"]["selector"]["select"]["options"] == [
        {"value": "", "label": "No parent"}
    ]
    # HA frontend 20260930.2 step-flow-form rejects required ""/undefined,
    # even though HA's Python schema accepts a Required field with default="".
    assert not fields["parent"]["required"]
    for name in ("members", "children", "missing_members", "remove_orphans"):
        assert not fields[name]["required"]
        assert fields[name]["default"] == []
    for name in ("children", "missing_members", "remove_orphans"):
        assert fields[name]["selector"]["select"]["options"] == []
    assert fields["name"]["required"]
    assert fields["power_policy"]["required"]

    data = {"name": "First Root", "power_policy": "RESTORE", "order": 0, "allow_moves": False}
    if with_members:
        data["members"] = members
    if not omit_optional:
        data.update(parent="", members=members, children=[], missing_members=[], remove_orphans=[])
    # The actual frontend required-field gate must allow this submission,
    # including zero and false. An untouched No parent is sent as an omission.
    frontend_data = {name: field["default"] for name, field in fields.items() if "default" in field}
    frontend_data.update(data)
    assert all(
        not field["required"] or frontend_data.get(name) not in ("", None)
        for name, field in fields.items()
    )
    normalized = form["data_schema"](data)
    assert normalized["parent"] == ""
    assert normalized["order"] == 0
    assert normalized["allow_moves"] is False
    assert normalized["members"] == members
    for name in ("children", "missing_members", "remove_orphans"):
        assert normalized[name] == []
    entry = await save_group(hass, form, data)
    manager = await get_manager(hass)
    group = manager.snapshot.tree.groups[entry.entry_id]
    assert group.parent is None
    assert group.order == 0
    assert group.policy.value == "RESTORE"
    assert len(manager.snapshot.tree.leaves) == len(members)
    assert not manager.snapshot.tree.children.get(entry.entry_id)
    assert not [event for event in calls if event.data["domain"] == "light"]


@pytest.mark.parametrize("relationship", ["disjoint", "children", "parent"])
async def test_later_group_with_child_choices(hass, relationship):
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, calls.append)
    first_form, _ = await group_form(hass)
    child = await save_group(hass, first_form, {"name": "Child"})
    form, fields = await group_form(hass)
    assert not fields["children"]["required"]
    assert {option["value"] for option in fields["children"]["selector"]["select"]["options"]} == {
        child.entry_id
    }
    # An available selector still validates selected IDs; optional does not mean arbitrary.
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            form["flow_id"], {"name": "Parent", "children": ["unknown-group"]}
        )
    data = {"name": "Later Group", "order": 0, "allow_moves": False}
    if relationship == "children":
        data["children"] = [child.entry_id]
    elif relationship == "parent":
        data["parent"] = child.entry_id
    later = await save_group(hass, form, data)
    manager = await get_manager(hass)
    assert manager.snapshot.tree.groups[child.entry_id].parent == (
        later.entry_id if relationship == "children" else None
    )
    assert manager.snapshot.tree.groups[later.entry_id].parent == (
        child.entry_id if relationship == "parent" else None
    )
    assert not [event for event in calls if event.data["domain"] == "light"]


async def test_group_form_keeps_required_validation(hass):
    form, fields = await group_form(hass)
    assert fields["name"]["required"]
    for invalid in ({}, {"name": ""}, {"name": "   "}):
        result = await hass.config_entries.flow.async_configure(form["flow_id"], invalid)
        assert result["step_id"] == "group"
        assert result["errors"] == {"base": "invalid_configuration"}
    for invalid in (
        {"name": "Root", "parent": "unknown-group"},
        {"name": "Root", "power_policy": "unknown-policy"},
        {"name": "Root", "order": -1},
        {"name": "Root", "children": False},
        {"name": "Root", "remove_orphans": False},
    ):
        with pytest.raises(InvalidData):
            await hass.config_entries.flow.async_configure(form["flow_id"], invalid)
