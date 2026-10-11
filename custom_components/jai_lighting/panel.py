"""Supported custom sidebar panel and authenticated admin management API."""

import asyncio
from pathlib import Path

import probatio as vol
from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .management import apply, build_candidate, inventory, preview, selected_lights
from .manager import get_manager
from .models import ModelError
from .presets import Target

KEY = f"{DOMAIN}_panel"


def result_data(manager):
    registry = er.async_get(manager.hass)
    return manager.snapshot.dump() | {
        "routes": manager.routes(),
        "entities": {
            id_: registry.async_get_entity_id(domain, DOMAIN, id_)
            for domain, ids in (
                ("light", manager.snapshot.tree.groups),
                ("scene", manager.snapshot.presets),
            )
            for id_ in ids
        },
        "inventory": inventory(manager.hass),
        "recovery_pending": bool(manager.repo.journal),
        "last_operation": manager.outcome,
    }


async def creation(manager, kind, data, expected):
    """Native flows remain the sole creator of HA group/preset host entries."""
    hass = manager.hass
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow_id = flow["flow_id"]
    try:
        if manager.snapshot.config_revision != expected:
            raise ModelError("Configuration changed; refresh before creating")
        flow = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": kind})
        if kind == "group":
            flow = await hass.config_entries.flow.async_configure(flow_id, data)
        elif kind == "preset":
            flow = await hass.config_entries.flow.async_configure(flow_id, {"name": data["name"]})
            for row in data["targets"]:
                target = Target(row["kind"], row["id"], row["properties"])
                values = dict(target.properties)
                flow = await hass.config_entries.flow.async_configure(
                    flow_id, {"next_step_id": "target"}
                )
                native = {
                    "target": f"{target.kind}:{target.id}",
                    "power": values.get("power", "inherit"),
                }
                for property_, switch in (
                    ("brightness_pct", "brightness"),
                    ("color_temp_kelvin", "ct"),
                    ("effect", "effect"),
                    ("color", "color"),
                ):
                    native[f"assign_{switch}"] = property_ in values
                    if property_ in values and property_ != "color":
                        native[property_] = values[property_]
                if color := values.get("color"):
                    native["color_kind"] = color.kind
                    if color.kind == "hs":
                        native["hue"], native["saturation"] = color.value
                    elif color.kind == "xy":
                        native["x"], native["y"] = color.value
                    else:
                        native["rgb"] = list(color.value)
                flow = await hass.config_entries.flow.async_configure(flow_id, native)
                if flow["type"] != "menu":
                    raise ModelError(str(flow.get("errors", "Invalid preset target")))
            flow = await hass.config_entries.flow.async_configure(
                flow_id, {"next_step_id": "preset_preview"}
            )
        if flow.get("errors") or flow.get("step_id") not in {"group_preview", "preset_preview"}:
            details = flow.get("description_placeholders", {})
            raise ModelError(
                details.get("error")
                or details.get("preview")
                or str(flow.get("errors", "Invalid creation draft"))
            )
        return {"flow_id": flow_id, "preview": flow["description_placeholders"]["preview"]}
    except Exception:
        hass.config_entries.flow.async_abort(flow_id)
        raise


@websocket_api.websocket_command(
    {
        "type": "jai_lighting/manager",
        vol.Required("command"): vol.In(
            [
                "get",
                "select",
                "preview",
                "apply",
                "create",
                "save_creation",
                "cancel_creation",
                "control",
                "activate",
            ]
        ),
        vol.Optional("revision"): vol.All(int, vol.Range(min=0)),
        vol.Optional("operations"): [dict],
        vol.Optional("filters", default={}): dict,
        vol.Optional("kind"): vol.In(["group", "preset"]),
        vol.Optional("data"): dict,
        vol.Optional("flow_id"): str,
        vol.Optional("collections"): [str],
        vol.Optional("properties"): dict,
        vol.Optional("preset_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_manager(hass, connection, msg):
    manager = await get_manager(hass)
    command = msg["command"]
    try:
        if command == "get":
            async with manager.lock:
                result = result_data(manager)
        elif command == "select":
            result = selected_lights(hass, msg["filters"])
        elif command == "preview":
            async with manager.lock:
                if msg["revision"] != manager.snapshot.config_revision:
                    raise ModelError("Configuration changed; refresh before editing")
                candidate = build_candidate(hass, manager.snapshot, msg["operations"])
                result = {
                    "snapshot": candidate.dump(),
                    "presets_before": preview(manager.snapshot),
                    "presets_after": preview(candidate),
                }
        elif command == "apply":
            result = await apply(manager, msg["revision"], msg["operations"], with_undo=True)
        elif command == "create":
            if msg["revision"] != manager.snapshot.config_revision:
                raise ModelError("Configuration changed; refresh before creating")
            result = await creation(manager, msg["kind"], msg["data"], msg["revision"])
            flow_id = result["flow_id"]
            key = f"{DOMAIN}:creation:{flow_id}"

            # Only this authenticated connection can complete its private draft.
            def cancel():
                hass.data.get(f"{DOMAIN}_drafts", {}).pop(flow_id, None)
                if any(
                    flow["flow_id"] == flow_id for flow in hass.config_entries.flow.async_progress()
                ):
                    hass.config_entries.flow.async_abort(flow_id)

            connection.subscriptions[key] = cancel
        elif command in {"save_creation", "cancel_creation"}:
            flow_id = msg["flow_id"]
            # Store ownership separately from client-supplied flow identifiers.
            owned = hass.data.setdefault(f"{DOMAIN}_drafts", {})
            if owned.get(flow_id) is not connection:
                raise ModelError("Creation draft belongs to another connection or expired")
            if command == "save_creation":
                result = await hass.config_entries.flow.async_configure(flow_id, {"action": "save"})
                if result["type"] != "create_entry":
                    raise ModelError(str(result.get("errors", "Creation failed")))
                result = {"entry_id": result["result"].entry_id}
            else:
                hass.config_entries.flow.async_abort(flow_id)
                result = {}
            owned.pop(flow_id, None)
            connection.subscriptions.pop(f"{DOMAIN}:creation:{flow_id}", None)
        elif command == "control":
            targets = [
                Target("collection", id_, msg["properties"])
                for id_ in dict.fromkeys(msg["collections"])
            ]
            result = await manager.execute(
                "control", Context(user_id=connection.user.id), targets=targets, response=True
            )
        else:
            result = await manager.execute(
                "preset",
                Context(user_id=connection.user.id),
                preset_id=msg["preset_id"],
                response=True,
            )
        if command == "create":
            hass.data.setdefault(f"{DOMAIN}_drafts", {})[result["flow_id"]] = connection
        connection.send_result(msg["id"], result)
    except (ModelError, HomeAssistantError, OSError, KeyError, TypeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_configuration", str(err))


async def async_setup_panel(hass):
    if KEY not in hass.data:
        websocket_api.async_register_command(hass, websocket_manager)
        hass.data[KEY] = {"registered": False, "lock": asyncio.Lock()}
    # Headless test/runtime instances retain the same API without a UI dependency.
    state = hass.data[KEY]
    async with state["lock"]:
        if state["registered"] or "frontend" not in hass.config.components:
            return
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    "/jai_lighting_static", str(Path(__file__).parent / "frontend"), False
                )
            ]
        )
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path="jai-lighting",
            webcomponent_name="jai-lighting-manager",
            sidebar_title="J'ai Lighting",
            sidebar_icon="mdi:lightbulb-group",
            module_url="/jai_lighting_static/manager.js",
            require_admin=True,
            config_panel_domain=DOMAIN,
        )
        state["registered"] = True
