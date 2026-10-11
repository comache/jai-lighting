"""Real HA websocket dispatch, native entry creation and physical light services."""

import asyncio
import json
import logging
import socket

from homeassistant.auth import auth_manager_from_config
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components import frontend
from homeassistant.components.light import DATA_COMPONENT, ColorMode
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.setup import async_setup_component
from test_runtime_v2 import Device, group

from custom_components.jai_lighting.manager import get_manager
from custom_components.jai_lighting.panel import async_setup_panel


async def client(hass, *, admin=True):
    await async_setup_panel(hass)
    if not hasattr(hass, "auth"):
        hass.auth = await auth_manager_from_config(hass, [], [])
        await hass.auth.async_create_user("Test owner", group_ids=[GROUP_ID_ADMIN])
    user = await hass.auth.async_create_user(
        "Manager test user", group_ids=[GROUP_ID_ADMIN if admin else GROUP_ID_USER]
    )
    queue = asyncio.Queue()

    def receive(message):
        queue.put_nowait(json.loads(message) if isinstance(message, (str, bytes)) else message)

    connection = ActiveConnection(
        logging.LoggerAdapter(logging.getLogger(__name__), {}),
        hass,
        receive,
        user,
        None,
        "127.0.0.1",
    )
    counter = 0

    async def request(command, **values):
        nonlocal counter
        counter += 1
        connection.async_handle(
            {"id": counter, "type": "jai_lighting/manager", "command": command} | values
        )
        return await asyncio.wait_for(queue.get(), 20)

    return connection, request


async def ok(request, command, **values):
    response = await request(command, **values)
    assert response["success"], response
    return response["result"]


async def test_admin_authorization_and_private_creation_cleanup(hass):
    restricted, request = await client(hass, admin=False)
    result = await request("get")
    assert not result["success"]
    assert result["error"]["code"] == "unauthorized"
    restricted.async_handle_close()
    connection, request = await client(hass)
    draft = await ok(request, "create", kind="group", revision=0, data={"name": "Empty root"})
    assert not (await get_manager(hass)).snapshot.tree.groups
    other, other_request = await client(hass)
    denied = await other_request("save_creation", flow_id=draft["flow_id"])
    assert not denied["success"]
    other.async_handle_close()
    connection.async_handle_close()
    assert not hass.config_entries.flow.async_progress()
    assert not hass.data["jai_lighting_drafts"]


async def test_panel_creation_rename_move_and_delete_preserve_existing_entity(hass):
    physical = Device("one", {ColorMode.BRIGHTNESS})
    await hass.data[DATA_COMPONENT].async_add_entities([physical])
    entry = await group(hass, "Original", [physical.entity_id])
    connection, request = await client(hass)
    initial = await ok(request, "get")
    entity_id = initial["entities"][entry.entry_id]
    draft = await ok(
        request,
        "create",
        kind="group",
        revision=initial["config_revision"],
        data={"name": "New parent"},
    )
    created = await ok(request, "save_creation", flow_id=draft["flow_id"])
    parent_id = created["entry_id"]
    snapshot = await ok(request, "get")
    await ok(
        request,
        "apply",
        revision=snapshot["config_revision"],
        operations=[
            {"action": "update_group", "id": entry.entry_id, "name": "Renamed"},
            {
                "action": "move",
                "parent": parent_id,
                "items": [{"kind": "group", "id": entry.entry_id}],
            },
        ],
    )
    await hass.async_block_till_done()
    current = await ok(request, "get")
    assert current["entities"][entry.entry_id] == entity_id
    assert hass.states.get(entity_id).attributes["parent_group_id"] == parent_id
    assert entry.title == "Renamed"
    await ok(
        request,
        "apply",
        revision=current["config_revision"],
        operations=[{"action": "delete_group", "id": parent_id, "confirm": True}],
    )
    final = await ok(request, "get")
    assert entry.entry_id in final["groups"]
    assert final["groups"][entry.entry_id]["parent_group_id"] is None
    assert final["entities"][entry.entry_id] == entity_id
    assert not physical.calls
    connection.async_handle_close()


async def test_collection_controls_overlap_and_native_preset_scene(hass):
    devices = [
        Device("one", {ColorMode.BRIGHTNESS}, on=False),
        Device("two", {ColorMode.BRIGHTNESS}, on=False),
    ]
    await hass.data[DATA_COMPONENT].async_add_entities(devices)
    entry = await group(hass, "Existing", [devices[0].entity_id])
    connection, request = await client(hass)
    current = await ok(request, "get")
    await ok(
        request,
        "apply",
        revision=current["config_revision"],
        operations=[
            {
                "action": "update_collection",
                "id": "a",
                "name": "A",
                "entity_ids": [d.entity_id for d in devices],
            },
            {
                "action": "update_collection",
                "id": "b",
                "name": "B",
                "entity_ids": [devices[0].entity_id],
            },
        ],
    )
    assert not any(device.calls for device in devices)
    result = await ok(
        request,
        "control",
        collections=["a", "b", "a"],
        properties={"power": "on", "brightness_pct": 30},
    )
    assert result["attempted"] == 2
    assert result["status"] == "success", result
    assert [len(device.calls) for device in devices] == [1, 1]
    assert all(device.calls[0][2].user_id == connection.user.id for device in devices)
    current = await ok(request, "get")
    one = current["collections"]["b"]["members"][0]
    draft = await ok(
        request,
        "create",
        kind="preset",
        revision=current["config_revision"],
        data={
            "name": "Collection recipe",
            "targets": [
                {"kind": "group", "id": entry.entry_id, "properties": {"brightness_pct": 20}},
                {"kind": "collection", "id": "a", "properties": {"brightness_pct": 40}},
                {"kind": "leaf", "id": one, "properties": {"brightness_pct": 60}},
            ],
        },
    )
    scene = await ok(request, "save_creation", flow_id=draft["flow_id"])
    current = await ok(request, "get")
    scene_id = current["entities"][scene["entry_id"]]
    assert hass.states.get(scene_id).attributes["jai_preset"]
    await hass.services.async_call("scene", "turn_on", {"entity_id": scene_id}, blocking=True)
    assert [device.brightness for device in devices] == [153, 102]
    assert current["leaves"][one]["parent_group_id"] == entry.entry_id
    connection.async_handle_close()


async def test_simultaneous_collection_calls_serialize(hass):
    device = Device("one", {ColorMode.BRIGHTNESS})
    await hass.data[DATA_COMPONENT].async_add_entities([device])
    first, request = await client(hass)
    await ok(
        request,
        "apply",
        revision=0,
        operations=[
            {
                "action": "update_collection",
                "id": "a",
                "name": "A",
                "entity_ids": [device.entity_id],
            }
        ],
    )
    second, other = await client(hass)
    result = await asyncio.gather(
        ok(request, "control", collections=["a"], properties={"power": "off"}),
        ok(other, "control", collections=["a"], properties={"power": "on", "brightness_pct": 50}),
    )
    assert all(outcome["status"] == "success" for outcome in result)
    assert device.is_on
    assert device.brightness == 128
    first.async_handle_close()
    second.async_handle_close()


async def test_deferred_off_slider_and_child_propagation_are_separate(hass):
    device = Device("one", {ColorMode.BRIGHTNESS}, on=False)
    await hass.data[DATA_COMPONENT].async_add_entities([device])
    child = await group(hass, "Child", [device.entity_id])
    parent = await group(hass, "Parent", children=[child.entry_id])
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_parent", "brightness_pct": 50},
        blocking=True,
    )
    assert not device.calls
    assert not device.is_on
    # Record the accepted HA boundary instead of silently changing it in the manager.
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.jai_parent", "brightness_pct": 30}, blocking=True
    )
    await hass.async_block_till_done()
    assert device.is_on
    assert hass.states.get("light.jai_child").state == "on"
    assert child.runtime_data.engine.master == 255
    # Native HA percentage preprocessing rounds 76.5 to 76 before our entity.
    assert parent.runtime_data.engine.master == 76


async def test_supported_panel_registration_is_admin_only_and_idempotent(hass):
    connection, _ = await client(hass)
    with socket.socket() as temporary_socket:
        temporary_socket.bind(("127.0.0.1", 0))
        port = temporary_socket.getsockname()[1]
    assert await async_setup_component(
        hass, "http", {"http": {"server_host": "127.0.0.1", "server_port": port}}
    )
    # Enable the frontend-present registration branch in this headless instance.
    # The actual HA custom-panel registry and HTTP static-path API run below.
    hass.config.components.add("frontend")
    await asyncio.gather(async_setup_panel(hass), async_setup_panel(hass))
    panel = hass.data[frontend.DATA_PANELS]["jai-lighting"].to_response()
    assert panel["require_admin"]
    assert panel["config"]["_panel_custom"]["name"] == "jai-lighting-manager"
    assert panel["config"]["_panel_custom"]["module_url"] == "/jai_lighting_static/manager.js"
    assert panel["config_panel_domain"] == "jai_lighting"
    connection.async_handle_close()
