"""Dashboard API tests against pinned HA, never production devices."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.auth import auth_manager_from_config
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components.light import DATA_COMPONENT, ColorMode
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from test_runtime_v2 import Device, group

from custom_components.jai_lighting.dashboard import (
    KEY,
    DashboardStore,
    initial_config,
    validate_config,
)
from custom_components.jai_lighting.management import apply
from custom_components.jai_lighting.manager import get_manager
from custom_components.jai_lighting.models import ModelError
from custom_components.jai_lighting.panel import async_setup_panel


async def api(hass, admin=True):
    await async_setup_panel(hass)
    if not hasattr(hass, "auth"):
        hass.auth = await auth_manager_from_config(hass, [], [])
        await hass.auth.async_create_user("Owner", group_ids=[GROUP_ID_ADMIN])
    user = await hass.auth.async_create_user(
        "Dashboard", group_ids=[GROUP_ID_ADMIN if admin else GROUP_ID_USER]
    )
    queue = asyncio.Queue()
    connection = ActiveConnection(
        logging.LoggerAdapter(logging.getLogger(__name__), {}),
        hass,
        lambda msg: queue.put_nowait(json.loads(msg) if isinstance(msg, (str, bytes)) else msg),
        user,
        None,
        "127.0.0.1",
    )
    counter = 0

    async def request(command, **data):
        nonlocal counter
        counter += 1
        connection.async_handle(
            {"id": counter, "type": "jai_lighting/dashboard", "command": command, **data}
        )
        return await asyncio.wait_for(queue.get(), 20)

    return connection, request


async def test_discovery_registry_identity_and_read_only_default(hass):
    d = Device("discovery", {ColorMode.BRIGHTNESS}, on=False)
    await hass.data[DATA_COMPONENT].async_add_entities([d])
    entry = await group(hass, "Interior", [d.entity_id])
    registry = er.async_get(hass)
    original = registry.async_get_entity_id("light", "jai_lighting", entry.entry_id)
    registry.async_update_entity(original, new_entity_id="light.renamed_actual_id")
    floor = fr.async_get(hass).async_create("Interior")
    area = ar.async_get(hass).async_create("Example room", floor_id=floor.floor_id)
    registry.async_update_entity(d.entity_id, area_id=area.id)
    connection, request = await api(hass)
    manager = await get_manager(hass)
    await hass.async_block_till_done()
    before = manager.snapshot.dump()
    result = await request("get")
    assert result["success"], result
    data = result["result"]
    assert data["catalog"]["groups"][entry.entry_id]["entity_id"] == "light.renamed_actual_id"
    assert data["config"]["sidebar"]["general"] == {"kind": "group", "id": entry.entry_id}
    assert data["catalog"]["floors"] == [{"id": floor.floor_id, "name": "Interior"}]
    assert data["catalog"]["entities"][d.entity_id]["floor_id"] == floor.floor_id
    assert not data["saved"]
    assert manager.snapshot.dump() == before
    assert not d.calls
    assert hass.data[KEY].data is None
    connection.async_handle_close()


async def test_designer_persistence_conflict_and_store_failure(hass):
    connection, request = await api(hass)
    data = (await request("get"))["result"]
    config = data["config"]
    config["title"] = "Saved building"
    result = await request("save", config=config, revision=0)
    assert result["success"], result
    loaded = await DashboardStore(hass).load()
    assert loaded["config"]["title"] == "Saved building"
    assert loaded["revision"] == 1
    assert not (await request("save", config=config, revision=0))["success"]
    with patch.object(hass.data[KEY].store, "async_save", AsyncMock(side_effect=OSError("disk"))):
        config["title"] = "Must not replace"
        assert not (await request("save", config=config, revision=1))["success"]
    assert (await request("get"))["result"]["config"]["title"] == "Saved building"
    assert (await get_manager(hass)).snapshot.config_revision == 0
    connection.async_handle_close()


async def test_operator_can_read_but_cannot_design(hass):
    connection, request = await api(hass, admin=False)
    result = await request("get")
    assert result["success"]
    denied = await request("save", config=result["result"]["config"], revision=0)
    assert denied["error"]["code"] == "unauthorized"
    connection.async_handle_close()


async def test_collection_absolute_controls_are_explicit_and_permission_checked(hass):
    d = Device("collection", {ColorMode.BRIGHTNESS}, on=False)
    await hass.data[DATA_COMPONENT].async_add_entities([d])
    await group(hass, "Group", [d.entity_id])
    manager = await get_manager(hass)
    await apply(
        manager,
        manager.snapshot.config_revision,
        [
            {
                "action": "update_collection",
                "id": "example",
                "name": "Selection",
                "entity_ids": [d.entity_id],
            }
        ],
    )
    connection, request = await api(hass, admin=False)
    assert (await request("get"))["result"]["catalog"]["collections"]["example"]["entities"] == [
        d.entity_id
    ]
    assert not d.calls
    denied = await request("collection", collection_id="example", properties={"master": 30})
    assert not denied["success"]
    masters = dict(manager.snapshot.runtime.masters)
    result = await request(
        "collection", collection_id="example", properties={"power": "on", "brightness_pct": 25}
    )
    assert result["success"], result
    assert d.is_on and d.brightness in (63, 64)
    assert manager.snapshot.runtime.masters == masters
    with patch.object(type(connection.user.permissions), "check_entity", return_value=False):
        assert not (await request("get"))["result"]["catalog"]["groups"]
        assert (await request("collection", collection_id="example", properties={"power": "off"}))[
            "error"
        ]["code"] == "unauthorized"
    connection.async_handle_close()


def test_layout_boundary_validation_and_empty_discovery():
    config = initial_config(
        {"groups": {}, "entities": {}, "floors": [], "collections": {}, "presets": {}}
    )
    assert config["sidebar"]["spotify"] is None
    assert not config["tabs"][0]["cards"]
    config["tabs"][0]["hidden"] = True
    with pytest.raises(ModelError):
        validate_config(config)
    config["tabs"][0]["hidden"] = False
    config["tabs"].append(dict(config["tabs"][0]))
    with pytest.raises(ModelError):
        validate_config(config)


async def test_corrupt_layout_is_retained_not_reset(hass):
    store = DashboardStore(hass)
    with (
        patch.object(
            store.store, "async_load", AsyncMock(return_value={"revision": -1, "config": {}})
        ),
        patch.object(store.store, "async_save", AsyncMock()) as save,
    ):
        with pytest.raises(ModelError):
            await store.load()
        save.assert_not_called()
        assert not store.loaded
