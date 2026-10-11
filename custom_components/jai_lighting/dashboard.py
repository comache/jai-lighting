"""Independent, revisioned dashboard layouts and permission-aware discovery.

Reading, designing and starting a dashboard never dispatches hardware commands.
Only the explicit collection action below enters the existing lighting engine.
"""

import asyncio
import re
import unicodedata
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

import probatio as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.storage import Store

from .manager import get_manager
from .models import ModelError
from .presets import Target

KEY = "jai_lighting.dashboard"
EVENT = "jai_lighting_dashboard_updated"
DOMAINS = {"light", "switch", "input_boolean", "sensor", "binary_sensor", "weather", "media_player"}
TEXT = vol.All(str, vol.Length(min=1, max=160))
REFERENCE = vol.Schema(
    {
        vol.Required("kind"): vol.In(["group", "entity", "collection", "preset"]),
        vol.Required("id"): TEXT,
    }
)
REFERENCES = vol.All([REFERENCE], vol.Length(max=30))
CARD = vol.Schema(
    {
        vol.Required("id"): TEXT,
        vol.Required("target"): REFERENCE,
        vol.Optional("title", default=""): vol.All(str, vol.Length(max=160)),
        vol.Optional("icon", default="mdi:lightbulb-group-outline"): vol.All(
            str, vol.Match(r"^mdi:[a-z0-9-]{1,80}$")
        ),
        vol.Optional("accent", default="lavender"): vol.In(
            ["lavender", "peach", "mint", "blue", "rose"]
        ),
        vol.Optional("size", default="medium"): vol.In(["small", "medium", "wide"]),
        vol.Optional("quick_brightness", default=False): bool,
        vol.Optional("quick", default=list): vol.All([REFERENCE], vol.Length(max=8)),
    }
)
TAB = vol.Schema(
    {
        vol.Required("id"): TEXT,
        vol.Required("name"): TEXT,
        vol.Optional("hidden", default=False): bool,
        vol.Optional("floor_id", default=None): vol.Any(None, TEXT),
        vol.Optional("cards", default=list): vol.All([CARD], vol.Length(max=100)),
        vol.Optional("moods", default=list): REFERENCES,
    }
)
CONFIG = vol.Schema(
    {
        vol.Required("version"): 1,
        vol.Required("title"): TEXT,
        vol.Required("sidebar"): {
            vol.Optional("general", default=None): vol.Any(None, REFERENCE),
            vol.Optional("service", default=None): vol.Any(None, REFERENCE),
            vol.Optional("spotify", default=None): vol.Any(None, REFERENCE),
            vol.Optional("shortcuts", default=list): REFERENCES,
        },
        vol.Required("tabs"): vol.All([TAB], vol.Length(min=1, max=30)),
    }
)


def validate_config(value):
    config = CONFIG(deepcopy(value))
    ids = [t["id"] for t in config["tabs"]]
    if len(set(ids)) != len(ids) or not any(not t["hidden"] for t in config["tabs"]):
        raise ModelError("Tabs need unique IDs and at least one visible tab")
    for tab in config["tabs"]:
        cards = [c["id"] for c in tab["cards"]]
        if len(set(cards)) != len(cards):
            raise ModelError("Card IDs must be unique within a tab")
        if any(ref["kind"] != "preset" for ref in tab["moods"]):
            raise ModelError("Moods must reference existing preset identities")
    return config


def discover(hass, manager, user):
    """Expose display metadata only, using registry identities and HA permissions."""
    registry, devices, areas = er.async_get(hass), dr.async_get(hass), ar.async_get(hass)

    def readable(entity_id):
        return bool(entity_id and user.permissions.check_entity(entity_id, POLICY_READ))

    entities = {}
    for state in hass.states.async_all():
        if state.domain not in DOMAINS or not readable(state.entity_id):
            continue
        entry = registry.async_get(state.entity_id)
        if entry and entry.disabled_by:
            continue
        device = devices.async_get(entry.device_id) if entry and entry.device_id else None
        area_id = (entry.area_id if entry else None) or (device.area_id if device else None)
        area = areas.async_get_area(area_id) if area_id else None
        entities[state.entity_id] = {
            "name": state.attributes.get("friendly_name", state.entity_id),
            "domain": state.domain,
            "area_id": area_id,
            "floor_id": area.floor_id if area else None,
            "platform": entry.platform if entry else None,
            "group_id": state.attributes.get("group_id")
            if entry and entry.platform == "jai_lighting"
            else None,
        }
    tree, routes = manager.snapshot.tree, manager.routes()
    group_entities = {
        id_: registry.async_get_entity_id("light", "jai_lighting", id_) for id_ in tree.groups
    }
    visible = {id_ for id_, entity_id in group_entities.items() if readable(entity_id)}
    groups = {
        id_: {
            "name": group.name,
            "entity_id": group_entities[id_],
            "parent": group.parent if group.parent in visible else None,
            "children": [c for c in tree.children.get(id_, ()) if c in visible],
            "lights": [
                routes[leaf] for leaf in tree.members.get(id_, ()) if readable(routes[leaf])
            ],
            "order": group.order,
        }
        for id_, group in tree.groups.items()
        if id_ in visible
    }
    collections = {
        id_: {"name": c.name, "entities": [routes[leaf] for leaf in c.members]}
        for id_, c in manager.snapshot.collections.items()
        if all(readable(routes[leaf]) for leaf in c.members)
    }
    presets = {}
    for id_, preset in manager.snapshot.presets.items():
        entity_id = registry.async_get_entity_id("scene", "jai_lighting", id_)
        if readable(entity_id):
            presets[id_] = {"name": preset.name, "entity_id": entity_id}
    visible_areas = {e["area_id"] for e in entities.values()}
    visible_floors = {e["floor_id"] for e in entities.values()}
    return {
        "groups": groups,
        "entities": entities,
        "collections": collections,
        "presets": presets,
        "floors": [
            {"id": f.floor_id, "name": f.name}
            for f in fr.async_get(hass).async_list_floors()
            if user.is_admin or f.floor_id in visible_floors
        ],
        "areas": [
            {"id": a.id, "name": a.name, "floor_id": a.floor_id}
            for a in areas.async_list_areas()
            if user.is_admin or a.id in visible_areas
        ],
        "topology_revision": manager.snapshot.config_revision,
    }


def initial_config(catalog):
    """Suggest a layout from discovery; no assumed IDs, writes or topology edits."""

    def normalized(name):
        return re.sub(
            r"[^a-z0-9]",
            "",
            unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower(),
        )

    groups = catalog["groups"]

    def named(name):
        matches = [id_ for id_, g in groups.items() if normalized(g["name"]) == name]
        return {"kind": "group", "id": matches[0]} if len(matches) == 1 else None

    tabs = [
        {"id": f"floor-{f['id']}", "name": f["name"], "floor_id": f["id"], "cards": [], "moods": []}
        for f in catalog["floors"]
    ]
    general, service = named("interior"), named("service")
    for name in ("interior", "terasa", "culoar", "culoarintrare"):
        ref = named(name)
        if ref and not any(
            normalized(t["name"]) == normalized(groups[ref["id"]]["name"]) for t in tabs
        ):
            tabs.append(
                {
                    "id": f"group-{ref['id']}",
                    "name": groups[ref["id"]]["name"],
                    "floor_id": None,
                    "cards": [],
                    "moods": [],
                }
            )
    if not tabs:
        tabs = [{"id": "lighting", "name": "Lumini", "floor_id": None, "cards": [], "moods": []}]

    def card(ref, quick=None):
        return {
            "id": uuid5(NAMESPACE_URL, f"jai-dashboard:{ref['kind']}:{ref['id']}").hex,
            "target": ref,
            "quick": quick or [],
        }

    for id_, group in sorted(groups.items(), key=lambda item: (item[1]["order"], item[1]["name"])):
        lineage, cursor = [], id_
        while cursor in groups and cursor not in lineage:
            lineage.append(cursor)
            cursor = groups[cursor]["parent"]
        entity = catalog["entities"].get(group["entity_id"], {})
        tab = next(
            (t for t in tabs if t["floor_id"] and t["floor_id"] == entity.get("floor_id")), None
        )
        if not tab:
            tab = next(
                (
                    t
                    for ancestor in lineage
                    for t in tabs
                    if normalized(t["name"]) == normalized(groups[ancestor]["name"])
                ),
                None,
            )
        if not tab and len(tabs) == 1 and tabs[0]["id"] == "lighting":
            tab = tabs[0]
        if (
            tab
            and len(tab["cards"]) < 100
            and {"kind": "group", "id": id_} not in (general, service)
        ):
            tab["cards"].append(
                card(
                    {"kind": "group", "id": id_},
                    [{"kind": "group", "id": c} for c in group["children"][:3]],
                )
            )
    for entity_id, entity in catalog["entities"].items():
        if entity["domain"] not in {"switch", "sensor", "binary_sensor", "weather"}:
            continue
        tab = next((t for t in tabs if t["floor_id"] and t["floor_id"] == entity["floor_id"]), None)
        if tab and len(tab["cards"]) < 100:
            tab["cards"].append(card({"kind": "entity", "id": entity_id}))
    spotify = [
        id_
        for id_, e in catalog["entities"].items()
        if e["platform"] == "spotify" and e["domain"] == "media_player"
    ]
    return validate_config(
        {
            "version": 1,
            "title": "J'ai Bistrot",
            "sidebar": {
                "general": general,
                "service": service,
                "spotify": {"kind": "entity", "id": spotify[0]} if len(spotify) == 1 else None,
                "shortcuts": [],
            },
            "tabs": tabs[:30],
        }
    )


class DashboardStore:
    def __init__(self, hass):
        self.store = Store(hass, 1, KEY)
        self.lock = asyncio.Lock()
        self.loaded = False
        self.data = None

    async def load(self):
        if not self.loaded:
            data = await self.store.async_load()
            if data is not None:
                if (
                    not isinstance(data, dict)
                    or type(data.get("revision")) is not int
                    or data["revision"] < 1
                ):
                    raise ModelError("Invalid dashboard revision; retained for repair")
                data = {"revision": data["revision"], "config": validate_config(data["config"])}
            self.data, self.loaded = data, True
        return self.data

    async def save(self, config, revision):
        async with self.lock:
            current = await self.load()
            if revision != (current["revision"] if current else 0):
                raise ModelError("Layout changed in another session. Reload before saving.")
            candidate = {"revision": revision + 1, "config": validate_config(config)}
            await self.store.async_save(candidate)
            self.data = candidate
            return deepcopy(candidate)


@websocket_api.websocket_command(
    {
        "type": "jai_lighting/dashboard",
        vol.Required("command"): vol.In(["get", "save", "collection"]),
        vol.Optional("config"): dict,
        vol.Optional("revision"): vol.All(int, vol.Range(min=0)),
        vol.Optional("collection_id"): TEXT,
        vol.Optional("properties"): dict,
    }
)
@websocket_api.async_response
async def websocket_dashboard(hass, connection, msg):
    try:
        manager = await get_manager(hass)
        store = hass.data[KEY]
        if msg["command"] == "get":
            async with manager.lock:
                catalog = discover(hass, manager, connection.user)
            async with store.lock:
                data = await store.load()
            # Non-admin readers do not receive private designer bindings.
            config = deepcopy(data["config"]) if data else initial_config(catalog)
            if not connection.user.is_admin:

                def allowed(ref):
                    return (
                        ref is None
                        or ref["id"]
                        in catalog[
                            {
                                "entity": "entities",
                                "group": "groups",
                                "collection": "collections",
                                "preset": "presets",
                            }[ref["kind"]]
                        ]
                    )

                config["sidebar"] = {
                    key: [r for r in value if allowed(r)]
                    if key == "shortcuts"
                    else value
                    if allowed(value)
                    else None
                    for key, value in config["sidebar"].items()
                }
                for tab in config["tabs"]:
                    tab["cards"] = [c for c in tab["cards"] if allowed(c["target"])]
                    for card in tab["cards"]:
                        card["quick"] = [r for r in card["quick"] if allowed(r)]
                    tab["moods"] = [r for r in tab["moods"] if allowed(r)]
            result = {
                "catalog": catalog,
                "config": config,
                "revision": data["revision"] if data else 0,
                "saved": bool(data),
            }
        elif msg["command"] == "save":
            if not connection.user.is_admin:
                raise Unauthorized()
            result = await store.save(msg["config"], msg["revision"])
            hass.bus.async_fire(EVENT, {"revision": result["revision"]})
        else:
            collection = manager.snapshot.collections[msg["collection_id"]]
            for leaf in collection.members:
                entity_id = manager.routes().get(leaf)
                if not entity_id or not connection.user.permissions.check_entity(
                    entity_id, POLICY_CONTROL
                ):
                    raise Unauthorized()
            if not msg["properties"] or set(msg["properties"]) - {"power", "brightness_pct"}:
                raise ModelError("Collections accept power and absolute brightness only")
            result = await manager.execute(
                "control",
                Context(user_id=connection.user.id),
                targets=[Target("leaf", leaf, msg["properties"]) for leaf in collection.members],
                response=True,
            )
        connection.send_result(msg["id"], result)
    except Unauthorized:
        connection.send_error(msg["id"], "unauthorized", "Not authorized for this action")
    except (
        ModelError,
        HomeAssistantError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        vol.Invalid,
    ) as err:
        connection.send_error(msg["id"], "dashboard_error", str(err))


def register_dashboard(hass):
    if KEY not in hass.data:
        hass.data[KEY] = DashboardStore(hass)
        websocket_api.async_register_command(hass, websocket_dashboard)
