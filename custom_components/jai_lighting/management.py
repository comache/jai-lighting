"""Revision-checked management drafts using the engine's existing journal."""

from dataclasses import replace
from uuid import uuid4

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from .collections import Collection
from .membership import identity_for, is_aggregate, resolve_identity
from .models import Leaf, ModelError
from .presets import Preset, Target, resolve
from .storage import Snapshot, encode_properties
from .topology import Topology


def inventory(hass):
    """Public registry metadata only; labels are temporary selection helpers."""
    entities, devices, areas = er.async_get(hass), dr.async_get(hass), ar.async_get(hass)
    ids = set(hass.states.async_entity_ids("light")) | {
        e.entity_id for e in entities.entities.values() if e.domain == "light"
    }
    lights = []
    for entity_id in sorted(ids):
        entry, state = entities.async_get(entity_id), hass.states.get(entity_id)
        if is_aggregate(hass, entity_id) or (entry and entry.disabled_by):
            continue
        device = devices.async_get(entry.device_id) if entry and entry.device_id else None
        area_id = (entry.area_id if entry else None) or (device.area_id if device else None)
        area = areas.async_get_area(area_id) if area_id else None
        labels = (
            (set(entry.labels) if entry else set())
            | (set(device.labels) if device else set())
            | (set(area.labels) if area else set())
        )
        lights.append(
            {
                "entity_id": entity_id,
                "name": (entry.name or entry.original_name if entry else None)
                or (state.attributes.get("friendly_name") if state else None)
                or entity_id,
                "area_id": area_id,
                "device_id": device.id if device else None,
                "labels": sorted(labels),
                "state": state.state if state else "missing",
            }
        )
    return {
        "lights": lights,
        "areas": [{"id": a.id, "name": a.name} for a in areas.async_list_areas()],
        "devices": [
            {"id": d.id, "name": d.name_by_user or d.name or d.id} for d in devices.devices.values()
        ],
        "labels": [
            {"id": label.label_id, "name": label.name}
            for label in lr.async_get(hass).async_list_labels()
        ],
    }


def selected_lights(hass, filters):
    """All filters combine; multiple label selections require every label."""
    if set(filters) - {"query", "area_id", "device_id", "labels"}:
        raise ModelError("Unknown inventory filter")
    query = filters.get("query", "").casefold()
    return [
        light["entity_id"]
        for light in inventory(hass)["lights"]
        if (not query or query in (light["name"] + " " + light["entity_id"]).casefold())
        and (not filters.get("area_id") or light["area_id"] == filters["area_id"])
        and (not filters.get("device_id") or light["device_id"] == filters["device_id"])
        and set(filters.get("labels", [])) <= set(light["labels"])
    ]


def build_candidate(hass, snapshot: Snapshot, operations: list[dict]) -> Snapshot:
    """Apply a whole draft to copies; validation runs before any durable mutation."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 1000:
        raise ModelError("Provide 1..1,000 management operations")
    groups, leaves = dict(snapshot.tree.groups), dict(snapshot.tree.leaves)
    collections, presets = dict(snapshot.collections), dict(snapshot.presets)

    def register_light(entity_id, parent=None, *, move=False):
        identity = identity_for(hass, entity_id)
        prior = next((leaf for leaf in leaves.values() if leaf.identity == identity), None)
        if prior:
            if move:
                leaves[prior.id] = replace(prior, parent=parent, order=next_order(parent, "leaf"))
            return prior.id
        id_ = uuid4().hex
        leaves[id_] = Leaf(id_, identity, parent, next_order(parent, "leaf"))
        return id_

    def next_order(parent, kind):
        records = groups if kind == "group" else leaves
        return (
            max(
                (record.order for record in records.values() if record.parent == parent), default=-1
            )
            + 1
        )

    for operation in operations:
        action = operation["action"]
        if action == "update_group":
            id_ = operation["id"]
            group = groups[id_]
            values = {
                key: operation[key]
                for key in ("name", "parent", "order", "policy")
                if key in operation
            }
            groups[id_] = replace(group, **values)
        elif action == "move":
            parent = operation.get("parent") or None
            items = operation["items"]
            if not items or len({(item["kind"], item["id"]) for item in items}) != len(items):
                raise ModelError("Select distinct records to move")
            tree = Topology(groups.values(), leaves.values())
            moving = {item["id"] for item in items if item["kind"] == "group"}
            for item in items:
                kind, id_ = item["kind"], item["id"]
                if kind not in {"group", "leaf"}:
                    raise ModelError("Only groups or physical lights can move")
                path = tree.ancestors(id_)[:-1] if kind == "group" else tree.leaf_path(id_)[:-1]
                if moving.intersection(path):
                    raise ModelError("Select the ancestor alone to preserve its subtree")
                records = groups if kind == "group" else leaves
                records[id_] = replace(records[id_], parent=parent, order=next_order(parent, kind))
        elif action == "reorder":
            kind, parent, ids = operation["kind"], operation.get("parent") or None, operation["ids"]
            if kind not in {"group", "leaf"}:
                raise ModelError("Invalid reorder kind")
            records = groups if kind == "group" else leaves
            siblings = {id_ for id_, record in records.items() if record.parent == parent}
            if len(set(ids)) != len(ids) or set(ids) != siblings:
                raise ModelError("Reorder must contain each sibling exactly once")
            for index, id_ in enumerate(ids):
                records[id_] = replace(records[id_], order=index)
        elif action == "add_lights":
            parent = operation["parent"]
            if parent not in groups:
                raise ModelError("Choose an existing group")
            entity_ids = operation["entity_ids"]
            if len(set(entity_ids)) != len(entity_ids):
                raise ModelError("Duplicate physical selection")
            for entity_id in entity_ids:
                identity = identity_for(hass, entity_id)
                prior = next((leaf for leaf in leaves.values() if leaf.identity == identity), None)
                if (
                    prior
                    and prior.parent not in (None, parent)
                    and not operation.get("confirm_moves")
                ):
                    raise ModelError("Confirm moving lights already owned by another group")
                if prior and prior.parent == parent:
                    continue
                register_light(entity_id, parent, move=True)
        elif action == "remove_lights":
            for id_ in operation["ids"]:
                leaves[id_] = replace(leaves[id_], parent=None, order=next_order(None, "leaf"))
        elif action == "delete_group":
            if operation.get("confirm") is not True:
                raise ModelError("Group deletion requires explicit confirmation")
            id_ = operation["id"]
            groups[id_]  # Require an existing record, including retained orphans.
            destination = operation.get("destination") or None
            if destination == id_:
                raise ModelError("Cannot move deleted group's contents into itself")
            for child_id, group in tuple(groups.items()):
                if group.parent == id_:
                    groups[child_id] = replace(
                        group, parent=destination, order=next_order(destination, "group")
                    )
            for leaf_id, leaf in tuple(leaves.items()):
                if leaf.parent == id_:
                    leaves[leaf_id] = replace(
                        leaf, parent=destination, order=next_order(destination, "leaf")
                    )
            del groups[id_]
        elif action == "update_collection":
            id_ = operation.get("id") or uuid4().hex
            if "entity_ids" in operation and "members" in operation:
                raise ModelError("Use leaf IDs or physical entity IDs, not both")
            old = collections.get(id_)
            members = tuple(operation.get("members", old.members if old else ()))
            if "entity_ids" in operation:
                ids = operation["entity_ids"]
                if len(set(ids)) != len(ids):
                    raise ModelError("Duplicate collection selection")
                members = tuple(register_light(entity_id) for entity_id in ids)
            collections[id_] = Collection(
                id_, operation.get("name", old.name if old else ""), members
            )
        elif action == "delete_collection":
            if operation.get("confirm") is not True:
                raise ModelError("Collection deletion requires explicit confirmation")
            del collections[operation["id"]]
        elif action == "update_preset":
            id_ = operation["id"]
            old = presets[id_]
            targets = (
                tuple(
                    Target(row["kind"], row["id"], row["properties"])
                    for row in operation["targets"]
                )
                if "targets" in operation
                else old.targets
            )
            presets[id_] = Preset(id_, operation.get("name", old.name), targets)
        elif action == "delete_preset":
            if operation.get("confirm") is not True:
                raise ModelError("Preset deletion requires explicit confirmation")
            del presets[operation["id"]]
        else:
            raise ModelError(f"Unknown management action: {action}")
    runtime = snapshot.runtime.copy()
    runtime.masters = {id_: value for id_, value in runtime.masters.items() if id_ in groups}
    candidate = replace(
        snapshot,
        tree=Topology(groups.values(), leaves.values()),
        runtime=runtime,
        presets=presets,
        collections=collections,
        config_revision=snapshot.config_revision + 1,
    )
    candidate.validate()
    candidate.tree.validate_routing(
        {id_: resolve_identity(hass, leaf.identity) for id_, leaf in leaves.items()}
    )
    return candidate


def preview(snapshot):
    return {
        id_: {
            leaf: {
                "properties": encode_properties(values),
                "sources": {
                    key: {"kind": source.kind, "id": source.id}
                    for key, source in effective.sources[leaf].items()
                },
            }
            for leaf, values in effective.values.items()
        }
        for id_, preset in snapshot.presets.items()
        for effective in [resolve(snapshot.tree, preset, collections=snapshot.collections)]
    }


async def apply(manager, expected, operations):
    """Serialize draft construction with controls; never overwrite newer runtime state."""
    async with manager.lock:
        if expected != manager.snapshot.config_revision:
            raise ModelError("Configuration changed; refresh before editing")
        if manager.repo.journal:
            raise ModelError("Configuration recovery required before editing")
        before = manager.snapshot
        candidate = build_candidate(manager.hass, before, operations)
        removed = (before.tree.groups.keys() - candidate.tree.groups.keys()) | (
            before.presets.keys() - candidate.presets.keys()
        )
        renamed = [
            {"id": id_, "kind": kind}
            for kind, old, new in (
                ("group", before.tree.groups, candidate.tree.groups),
                ("preset", before.presets, candidate.presets),
            )
            for id_, record in new.items()
            if id_ in old
            and old[id_].name != record.name
            and manager.hass.config_entries.async_get_entry(id_)
        ]
        if removed and renamed:
            raise ModelError("Rename and host deletion must be separate transactions")
        if removed:
            hosts = [{"id": id_, "removed": True} for id_ in removed]
            await manager.repo.prepare(candidate, hosts, kind="remove")
        else:
            await manager.repo.prepare(candidate, renamed)
        await manager.recover()
        return candidate.config_revision
