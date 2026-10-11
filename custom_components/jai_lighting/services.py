"""Native domain actions: coherent batches rather than per-entity callbacks."""

import math

import probatio as vol
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_extract_entity_ids

from .const import DOMAIN
from .engine import level
from .models import ModelError
from .presets import Target


def finite_percentage(value):
    if not math.isfinite(value):
        raise vol.Invalid("Master percentage must be finite")
    return value


def register_services(hass, manager):
    if hass.services.has_service(DOMAIN, "set_master"):
        return

    async def selected(call, kind):
        ids = await async_extract_entity_ids(call, expand_group=False)
        entries = er.async_get(hass)
        result = []
        for entity_id in ids:
            entry = entries.async_get(entity_id)
            if entry and entry.platform == DOMAIN:
                id_ = entry.unique_id
                if kind == "group" and id_ in manager.snapshot.tree.groups:
                    result.append(id_)
                elif kind == "preset" and id_ in manager.snapshot.presets:
                    result.append(id_)
                else:
                    raise HomeAssistantError("Target has the wrong J'ai Light Manager kind")
            elif kind == "control":
                matched = [id_ for id_, route in manager.routes().items() if route == entity_id]
                if not matched:
                    raise HomeAssistantError("Control targets must be configured physical leaves")
                result.extend(matched)
            else:
                raise HomeAssistantError("Target must be a J'ai Light Manager entity")
        if not result:
            raise HomeAssistantError("No J'ai Light Manager targets selected")
        return result

    async def set_master(call):
        groups = await selected(call, "group")
        await manager.execute(
            "master",
            call.context,
            groups=groups,
            master=level(call.data["brightness_pct"] * 255 / 100, 3),
        )

    async def control_targets(call, values):
        ids = await async_extract_entity_ids(call, expand_group=False)
        targets = []
        for entity_id in ids:
            registry_entry = er.async_get(hass).async_get(entity_id)
            if registry_entry and registry_entry.platform == DOMAIN:
                if registry_entry.unique_id not in manager.snapshot.tree.groups:
                    raise HomeAssistantError("Control requires a group or physical leaf")
                kind, id_ = "group", registry_entry.unique_id
            else:
                matching = [id_ for id_, route in manager.routes().items() if route == entity_id]
                if len(matching) != 1:
                    raise HomeAssistantError("Unconfigured physical leaf")
                kind, id_ = "leaf", matching[0]
            try:
                targets.append(Target(kind, id_, dict(values)))
            except ModelError as err:
                raise HomeAssistantError(str(err)) from err
        if not targets:
            raise HomeAssistantError("No targets selected")
        return targets

    async def control(call):
        if "assignments" in call.data:
            if "properties" in call.data or any(k in call.data for k in target_fields):
                raise HomeAssistantError("Use either target plus properties, or assignments")
            rows = call.data["assignments"]
            if not 1 <= len(rows) <= 1000:
                raise HomeAssistantError("Control requires 1..1,000 assignments")
            targets = []
            for row in rows:
                nested = ServiceCall(
                    hass, DOMAIN, "control", {"entity_id": row["entity_id"]}, call.context
                )
                targets.extend(await control_targets(nested, row["properties"]))
        elif "properties" in call.data:
            targets = await control_targets(call, call.data["properties"])
        else:
            raise HomeAssistantError("Provide sparse properties or explicit assignments")
        return await manager.execute(
            "control", call.context, targets=targets, response=call.return_response
        )

    async def activate(call):
        ids = await selected(call, "preset")
        if len(ids) != 1:
            raise HomeAssistantError("Activate one preset per coherent operation")
        return await manager.execute(
            "preset", call.context, preset_id=ids[0], response=call.return_response
        )

    async def topology(call):
        async with manager.lock:
            return manager.snapshot.dump() | {"routes": manager.routes(), "metadata_version": 1}

    target_fields = {
        vol.Optional("entity_id"): vol.Any(str, [str]),
        vol.Optional("device_id"): vol.Any(str, [str]),
        vol.Optional("area_id"): vol.Any(str, [str]),
        vol.Optional("floor_id"): vol.Any(str, [str]),
        vol.Optional("label_id"): vol.Any(str, [str]),
    }
    hass.services.async_register(
        DOMAIN,
        "set_master",
        set_master,
        schema=vol.Schema(
            target_fields
            | {
                vol.Required("brightness_pct"): vol.All(
                    vol.Coerce(float), finite_percentage, vol.Range(min=1, max=100)
                )
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "control",
        control,
        schema=vol.Schema(
            target_fields
            | {
                vol.Optional("properties"): dict,
                vol.Optional("assignments"): [
                    vol.Schema(
                        {
                            vol.Required("entity_id"): vol.Any(str, [str]),
                            vol.Required("properties"): dict,
                        }
                    )
                ],
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "activate_preset",
        activate,
        schema=vol.Schema(target_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "get_topology",
        topology,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
