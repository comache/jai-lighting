"""HA lifecycle for shared hierarchical lighting and absolute preset scenes."""

from dataclasses import replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryError

from .const import DOMAIN
from .coordinator import GroupCoordinator
from .manager import get_manager
from .models import ModelError
from .panel import async_setup_panel
from .services import register_services
from .topology import Topology

type JaiConfigEntry = ConfigEntry[GroupCoordinator]


async def async_setup(hass, config):
    await async_setup_panel(hass)
    return True


async def async_migrate_entry(hass, entry):
    if entry.version != 1:
        return entry.version == 2
    try:
        manager = await get_manager(hass)
        if entry.entry_id in manager.snapshot.tree.groups:
            hass.config_entries.async_update_entry(
                entry,
                version=2,
                minor_version=1,
                data={"kind": "group", "group_id": entry.entry_id},
                options={},
            )
        else:
            await manager.migrate()
        return True
    except (ModelError, OSError) as err:
        if (manager := hass.data.get(DOMAIN)) is not None:
            manager.issue("migration", str(err))
        return False


async def async_setup_entry(hass, entry):
    await async_setup_panel(hass)
    try:
        manager = await get_manager(hass)
        async with manager.lock:
            await manager.recover()
            kind = entry.data["kind"]
            if kind == "group":
                if entry.entry_id not in manager.snapshot.tree.groups:
                    raise ModelError("Missing group configuration; repair required")
                entry.runtime_data = GroupCoordinator(hass, entry.entry_id, manager)
                platforms = [Platform.LIGHT]
            elif kind == "preset":
                if entry.entry_id not in manager.snapshot.presets:
                    raise ModelError("Missing preset configuration; repair required")
                entry.runtime_data = manager
                platforms = [Platform.SCENE]
            else:
                raise ModelError("Unknown lighting config-entry kind")
            await manager.flush()
        register_services(hass, manager)
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
        return True
    except (ModelError, OSError) as err:
        raise ConfigEntryError(str(err)) from err


async def async_unload_entry(hass, entry):
    platform = Platform.LIGHT if entry.data["kind"] == "group" else Platform.SCENE
    return await hass.config_entries.async_unload_platforms(entry, [platform])


async def async_remove_entry(hass, entry):
    existing = hass.data.get(DOMAIN)
    if existing and existing.repo.journal and existing.repo.journal["kind"] == "remove":
        if any(spec["id"] == entry.entry_id for spec in existing.repo.journal["hosts"]):
            return
    manager = await get_manager(hass)
    async with manager.lock:
        snapshot = manager.snapshot
        if entry.entry_id in snapshot.tree.groups:
            affected = snapshot.tree.resolve(entry.entry_id)
            references = [
                p
                for p in snapshot.presets.values()
                if any(t.id == entry.entry_id for t in p.targets)
            ]
            if affected or snapshot.tree.children.get(entry.entry_id) or references:
                manager.issue(
                    f"orphan_{entry.entry_id}",
                    "Deleted group retained as blocked; move members/children and repair presets",
                )
                manager.notify()
                return
            groups = [g for g in snapshot.tree.groups.values() if g.id != entry.entry_id]
            runtime = snapshot.runtime.copy()
            runtime.masters.pop(entry.entry_id, None)
            candidate = replace(
                snapshot,
                tree=Topology(groups, snapshot.tree.leaves.values()),
                runtime=runtime,
                config_revision=snapshot.config_revision + 1,
            )
        else:
            candidate = replace(
                snapshot,
                presets={id_: p for id_, p in snapshot.presets.items() if id_ != entry.entry_id},
                config_revision=snapshot.config_revision + 1,
            )
        await manager.repo.prepare(
            candidate, [{"id": entry.entry_id, "removed": True}], kind="remove"
        )
        hass.async_create_task(manager.finish_removal())
