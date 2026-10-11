"""Shared HA adapter: serialized plans, durable state and safe physical dispatch."""

import asyncio
import json
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from homeassistant.components.light import LightEntityFeature
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_OFF, STATE_ON
from homeassistant.core import Context, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import async_get as restore_data
from homeassistant.util import color as color_util
from homeassistant.util import slugify

from .capabilities import absolute
from .const import DOMAIN, SERVICE_TIMEOUT, STATE_TIMEOUT
from .membership import identity_for, is_aggregate, resolve_identity
from .migration import preflight, raw_legacy_records
from .models import Capabilities, Group, Intention, Leaf, ModelError, Observation, Plan
from .planner import observe, power, relative
from .presets import Preset
from .presets import plan as preset_plan
from .presets import resolve as resolve_preset
from .storage import Repository, Snapshot, encode_properties
from .topology import Topology


class Manager:
    def __init__(self, hass, lock=None) -> None:
        self.hass = hass
        self.lock = lock or asyncio.Lock()
        self.load_lock = asyncio.Lock()
        self.repo = Repository(hass)
        self.loaded = False
        self.listeners = {}
        self.failures: dict[str, str] = {}
        self.outcome: dict[str, Any] = {}
        self._unsubscribe = None
        self._registry_unsubscribe = None
        self._stop_unsubscribe = None
        self._reconcile_task = None
        self.standalone = False

    @property
    def snapshot(self) -> Snapshot:
        return self.repo.snapshot

    async def load(self) -> None:
        async with self.load_lock:
            if self.loaded:
                return
            await self.repo.load()
            await self.recover()
            self.loaded = True
            for id_ in self.snapshot.tree.groups:
                if self.hass.config_entries.async_get_entry(id_) is None:
                    self.issue(
                        f"orphan_{id_}",
                        "Group host missing; branch retained and blocked. "
                        "Move members/children and repair presets.",
                    )
            self.clear_resolved_issues()
            self.subscribe()
            self._registry_unsubscribe = self.hass.bus.async_listen(
                "entity_registry_updated", self._changed
            )
            self._stop_unsubscribe = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._shutdown
            )

    async def _shutdown(self, event) -> None:
        async with self.lock:
            if not self.repo.journal:
                await self.flush()
        for unsub in (self._unsubscribe, self._registry_unsubscribe):
            if unsub:
                unsub()

    def routes(self) -> dict[str, str | None]:
        routes = {
            id_: resolve_identity(self.hass, leaf.identity)
            for id_, leaf in self.snapshot.tree.leaves.items()
        }
        self.snapshot.tree.validate_routing(routes)
        return routes

    def blocked(self, leaf_id: str) -> bool:
        if self.standalone:
            return False
        parent = self.snapshot.tree.leaves[leaf_id].parent
        if parent is None:
            return False
        for group_id in self.snapshot.tree.ancestors(parent):
            entry = self.hass.config_entries.async_get_entry(group_id)
            if entry is None or entry.disabled_by is not None:
                return True
        return False

    def observations(self) -> dict[str, Observation]:
        result = {}
        for id_, entity_id in self.routes().items():
            state = self.hass.states.get(entity_id) if entity_id else None
            registry_entry = er.async_get(self.hass).async_get(entity_id) if entity_id else None
            cached = self.snapshot.capabilities.get(id_, {})
            attributes = dict(state.attributes) if state else {}
            if not attributes.get("supported_color_modes") and registry_entry:
                attributes = dict(registry_entry.capabilities or {}) | attributes
            modes = attributes.get("supported_color_modes") or cached.get("modes", [])
            if not isinstance(modes, (list, tuple, set, frozenset)):
                modes = []
            modes = [m for m in modes if isinstance(m, str)]
            features = attributes.get("supported_features", 0)
            effects = attributes.get("effect_list", cached.get("effects", []))
            if not isinstance(effects, (list, tuple)):
                effects = []
            effects = [e for e in effects if isinstance(e, str) and e]
            low = attributes.get("min_color_temp_kelvin", cached.get("min_kelvin"))
            high = attributes.get("max_color_temp_kelvin", cached.get("max_kelvin"))
            low = low if type(low) is int and low > 0 else None
            high = high if type(high) is int and high > 0 else None
            if low and high and low > high:
                low = high = None
            caps = Capabilities(
                frozenset(modes)
                & {
                    "onoff",
                    "brightness",
                    "color_temp",
                    "hs",
                    "rgb",
                    "xy",
                    "rgbw",
                    "rgbww",
                    "white",
                },
                low,
                high,
                tuple(effects)
                if isinstance(features, int)
                and features & LightEntityFeature.EFFECT
                or "supported_features" not in attributes
                and cached.get("effects")
                else (),
            )
            brightness = attributes.get("brightness")
            valid = type(brightness) is int and 1 <= brightness <= 255
            available = bool(
                state
                and state.state in (STATE_ON, STATE_OFF)
                and not is_aggregate(self.hass, entity_id)
                and not self.blocked(id_)
                and not (registry_entry and registry_entry.disabled_by)
            )
            reason = (
                "registry_missing"
                if entity_id is None
                else "entity_missing"
                if state is None
                else "aggregate_rejected"
                if is_aggregate(self.hass, entity_id)
                else "branch_disabled"
                if self.blocked(id_)
                else "entity_disabled"
                if registry_entry and registry_entry.disabled_by
                else "unavailable"
            )
            result[id_] = Observation(
                available,
                bool(state and state.state == STATE_ON),
                brightness if valid else None,
                caps,
                attributes,
                reason,
            )
        return result

    def _with_runtime(self, runtime) -> Snapshot:
        return replace(
            self.snapshot,
            runtime=runtime,
            state_revision=self.snapshot.state_revision + 1,
            capabilities=dict(self.snapshot.capabilities),
        )

    async def flush(self) -> None:
        runtime = self.snapshot.runtime.copy()
        observations = self.observations()
        observe(runtime, observations)
        candidate = self._with_runtime(runtime)
        for id_, o in observations.items():
            if o.capabilities.modes:
                c = o.capabilities
                fresh = o.available and bool(o.properties.get("supported_color_modes"))
                candidate.capabilities[id_] = {
                    "modes": sorted(c.modes),
                    "min_kelvin": c.min_kelvin,
                    "max_kelvin": c.max_kelvin,
                    "effects": list(c.effects),
                    "stale": not fresh,
                    "observed_at": time.time()
                    if fresh
                    else self.snapshot.capabilities.get(id_, {}).get("observed_at"),
                }
        await self.repo.save(candidate)

    @callback
    def subscribe(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        self._unsubscribe = async_track_state_change_event(
            self.hass, [id_ for id_ in self.routes().values() if id_], self._changed
        )

    @callback
    def _changed(self, event) -> None:
        # Signal physical acknowledgement listeners independently of the lock.
        if event.event_type == "entity_registry_updated":
            try:
                self.subscribe()
            except ModelError as err:
                self.outcome = {"status": "invalid_topology", "error": str(err)}
        self.notify()
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = self.hass.async_create_task(self._reconcile())

    async def _reconcile(self) -> None:
        async with self.lock:
            if self.repo.journal:
                return
            try:
                await self.flush()
            except (ModelError, OSError, HomeAssistantError) as err:
                self.outcome.update(
                    status="storage_failed_after_dispatch"
                    if self.outcome.get("status") == "storage_failed_after_dispatch"
                    else "storage_or_topology_error",
                    reconciliation_error=str(err),
                )
            self.notify()

    @callback
    def notify(self) -> None:
        self.hass.bus.async_fire(
            "jai_lighting_manager_updated", {"config_revision": self.snapshot.config_revision}
        )
        for listener in tuple(self.listeners.values()):
            listener()

    def issue(self, id_: str, text: str) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            id_,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="configuration",
            translation_placeholders={"error": text},
        )

    @callback
    def clear_resolved_issues(self):
        for domain, id_ in tuple(ir.async_get(self.hass).issues):
            if domain != DOMAIN:
                continue
            if id_.startswith("orphan_"):
                group_id = id_.removeprefix("orphan_")
                if (
                    group_id not in self.snapshot.tree.groups
                    or self.hass.config_entries.async_get_entry(group_id)
                ):
                    ir.async_delete_issue(self.hass, DOMAIN, id_)
            elif id_ == "migration" and not any(
                e.version == 1 for e in self.hass.config_entries.async_entries(DOMAIN)
            ):
                ir.async_delete_issue(self.hass, DOMAIN, id_)
            elif id_ == "configuration_recovery" and not self.repo.journal:
                ir.async_delete_issue(self.hass, DOMAIN, id_)

    async def _host_durable(self, hosts: list[dict]) -> None:
        # Read-only check of pinned Core storage. Never call Store.load on Core's
        # file: it could rename corrupt JSON or migrate an unfamiliar minor schema.
        path = Path(self.hass.config.path(".storage", "core.config_entries"))

        def read_hosts():
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except ValueError as err:
                raise ModelError("Core entry storage unreadable; journal retained") from err
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != 1
                or envelope.get("minor_version") != 5
            ):
                raise ModelError("Unsupported Core entry storage schema; journal retained")
            return {e["entry_id"]: e for e in envelope["data"]["entries"]}

        for _ in range(100):
            entries = await self.hass.async_add_executor_job(read_hosts)
            if all(
                h["id"] not in entries
                if h.get("removed")
                else (
                    h["id"] in entries
                    and self.hass.config_entries.async_get_entry(h["id"]) is not None
                    and entries[h["id"]]["version"] == 2
                    and entries[h["id"]]["data"]
                    == self.hass.config_entries.async_get_entry(h["id"]).data
                    and entries[h["id"]]["title"]
                    == self.hass.config_entries.async_get_entry(h["id"]).title
                    and entries[h["id"]]["options"] == {}
                )
                for h in hosts
            ):
                return
            await asyncio.sleep(0.05)
        raise ModelError("HA host entries are not durable yet; recovery journal retained")

    async def recover(self) -> None:
        journal = self.repo.journal
        if not journal:
            return
        candidate = Snapshot.load(journal["candidate"])
        if journal["kind"] == "remove":
            # Replaying Core's public removal API is safe: the hook recognizes
            # this durable intent and does not prepare a second transaction.
            for spec in journal["hosts"]:
                if self.hass.config_entries.async_get_entry(spec["id"]) is not None:
                    await self.hass.config_entries.async_remove(spec["id"])
            await self._host_durable(journal["hosts"])
            await self.repo.finish()
            self.clear_resolved_issues()
            if self.loaded:
                self.subscribe()
                self.notify()
            return
        if journal["kind"] == "create":
            token = journal["token"]
            host = next(
                (
                    e
                    for e in self.hass.config_entries.async_entries(DOMAIN)
                    if e.data.get("creation_token") == token
                ),
                None,
            )
            if host is None:
                # No host was committed: abort only this unpublished draft.
                await self.repo.abort()
                return
            groups = [
                replace(g, id=host.entry_id)
                if g.id == token
                else replace(g, parent=host.entry_id)
                if g.parent == token
                else g
                for g in candidate.tree.groups.values()
            ]
            leaves = [
                replace(leaf, parent=host.entry_id) if leaf.parent == token else leaf
                for leaf in candidate.tree.leaves.values()
            ]
            runtime = candidate.runtime.copy()
            if token in runtime.masters:
                runtime.masters[host.entry_id] = runtime.masters.pop(token)
            presets = dict(candidate.presets)
            if token in presets:
                presets[host.entry_id] = replace(presets.pop(token), id=host.entry_id)
            presets = {
                id_: replace(
                    preset,
                    targets=tuple(
                        replace(target, id=host.entry_id)
                        if target.kind == "group" and target.id == token
                        else target
                        for target in preset.targets
                    ),
                )
                for id_, preset in presets.items()
            }
            candidate = replace(
                candidate, tree=Topology(groups, leaves), runtime=runtime, presets=presets
            )
            # Record assigned host ID before replacing its creation token.
            hosts = [{"id": host.entry_id, "kind": host.data["kind"]}]
            rewritten = journal | {
                "kind": "edit",
                "origin": "create",
                "candidate": candidate.dump(),
                "hosts": hosts,
            }
            await self.repo._write(self.snapshot, rewritten)
            self.repo.journal = rewritten
            journal = rewritten
        for spec in journal["hosts"]:
            host = self.hass.config_entries.async_get_entry(spec["id"])
            if host is None:
                if journal.get("origin") == "create":
                    await self.repo.abort()
                    return
                raise ModelError("Missing transaction host; legacy and journal preserved")
            kind = spec["kind"]
            name = (
                candidate.tree.groups[host.entry_id].name
                if kind == "group"
                else candidate.presets[host.entry_id].name
            )
            self.hass.config_entries.async_update_entry(
                host,
                version=2,
                minor_version=1,
                title=name,
                data={"kind": kind, f"{kind}_id": host.entry_id},
                options={},
            )
        await self._host_durable(journal["hosts"])
        await self.repo.finish()
        self.clear_resolved_issues()
        if self.loaded:
            self.subscribe()
            self.notify()

    async def finish_removal(self):
        # Core schedules its durable write after the remove hook returns.
        async with self.lock:
            try:
                await self.recover()
            except (ModelError, OSError) as err:
                self.issue("configuration_recovery", str(err))

    async def migrate(self, owners=None) -> None:
        async with self.lock:
            await self.recover()
            entries = [e for e in self.hass.config_entries.async_entries(DOMAIN) if e.version == 1]
            if not entries:
                return
            records, restored, identities, virtual_ids = [], {}, {}, set()
            for entry in entries:
                records.append(entry.as_dict())
                config = entry.options or entry.data
                entity_id = er.async_get(self.hass).async_get_entity_id(
                    "light", DOMAIN, entry.entry_id
                )
                entity_id = entity_id or f"light.jai_{slugify(config['name'])}"
                virtual_ids.add(entity_id)
                stored = restore_data(self.hass).last_states.get(entity_id)
                restored[entry.entry_id] = (
                    stored.extra_data.as_dict() if stored and stored.extra_data else {}
                )
                for member in config["members"]:
                    identities[member] = identity_for(self.hass, member, existing=True)
            candidate = preflight(records, restored, identities, owners, self.snapshot)
            self.validate_candidate(candidate)

            def original_records():
                raw = {}
                for key, filename, wanted, restore in (
                    ("raw_entries", "core.config_entries", {e.entry_id for e in entries}, False),
                    ("raw_restore", "core.restore_state", virtual_ids, True),
                ):
                    try:
                        text = Path(self.hass.config.path(".storage", filename)).read_text(
                            encoding="utf-8"
                        )
                    except FileNotFoundError:
                        text = ""
                    raw[key] = raw_legacy_records(text, wanted, restore=restore) if text else {}
                return raw

            raw = await self.hass.async_add_executor_job(original_records)
            await self.repo.prepare(
                candidate,
                [{"id": e.entry_id, "kind": "group"} for e in entries],
                kind="migration",
                legacy={"entries": records, "restore": restored} | raw,
            )
            await self.recover()

    async def commit(self, candidate: Snapshot, hosts: list[dict], expected: int) -> None:
        async with self.lock:
            if expected != self.snapshot.config_revision:
                raise ModelError("Configuration changed; refresh the form")
            candidate = self.rebase_runtime(candidate)
            self.validate_candidate(candidate)
            await self.repo.prepare(candidate, hosts)
            await self.recover()

    async def prepare_creation(self, candidate: Snapshot, token: str, expected: int) -> None:
        async with self.lock:
            if expected != self.snapshot.config_revision:
                raise ModelError("Configuration changed; refresh the form")
            candidate = self.rebase_runtime(candidate)
            self.validate_candidate(candidate)
            await self.repo.prepare(candidate, [], kind="create", token=token)

    def rebase_runtime(self, candidate: Snapshot) -> Snapshot:
        """A reviewed configuration draft must not revert intervening live controls."""
        runtime = self.snapshot.runtime.copy()
        runtime.masters = {
            id_: runtime.masters.get(id_, candidate.runtime.masters.get(id_, 255))
            for id_ in candidate.tree.groups
        }
        runtime.targets = {
            id_: value for id_, value in runtime.targets.items() if id_ in candidate.tree.leaves
        }
        return replace(
            candidate,
            runtime=runtime,
            state_revision=self.snapshot.state_revision + 1,
            capabilities={
                id_: record
                for id_, record in self.snapshot.capabilities.items()
                if id_ in candidate.tree.leaves
            },
        )

    def validate_candidate(self, candidate):
        candidate.validate()
        candidate.tree.validate_routing(
            {
                id_: resolve_identity(self.hass, leaf.identity)
                for id_, leaf in candidate.tree.leaves.items()
            }
        )

    def group_candidate(self, group_id: str, config: dict, *, presets=None) -> Snapshot:
        group = Group(
            group_id,
            config["name"].strip(),
            config.get("parent") or None,
            config.get("power_policy", "RESTORE"),
            config.get("order", 0),
        )
        existing = self.snapshot.tree
        groups = dict(existing.groups) | {group_id: group}
        leaves = {id_: leaf for id_, leaf in existing.leaves.items() if leaf.parent != group_id}
        owned = {leaf.identity.key: leaf for leaf in existing.leaves.values()}
        selected = set()
        for order, member in enumerate(config.get("members", [])):
            identity = identity_for(
                self.hass,
                member,
                existing=any(
                    resolve_identity(self.hass, leaf.identity) == member and leaf.parent == group_id
                    for leaf in existing.leaves.values()
                ),
            )
            if identity.key in selected:
                raise ModelError("Duplicate physical member")
            selected.add(identity.key)
            prior = owned.get(identity.key)
            if prior and prior.parent != group_id and not config.get("allow_moves"):
                raise ModelError(f"Physical light already owned by {prior.parent}; confirm move")
            leaf = (
                replace(prior, parent=group_id, order=order)
                if prior
                else Leaf(uuid4().hex, identity, group_id, order)
            )
            leaves[leaf.id] = leaf
        for id_ in config.get("missing_members", []):
            prior = existing.leaves.get(id_)
            if prior is None or prior.parent != group_id:
                raise ModelError("Invalid retained missing member")
            leaves[id_] = prior
        requested_children = set(config.get("children", []))
        for id_, child in tuple(groups.items()):
            if id_ in requested_children:
                if id_ == group_id:
                    raise ModelError("Self-parenting group")
                if child.parent not in (None, group_id) and not config.get("allow_moves"):
                    raise ModelError("Child group already owned; confirm move")
                groups[id_] = replace(child, parent=group_id)
            elif child.parent == group_id:
                groups[id_] = replace(child, parent=None)
        if requested_children - groups.keys():
            raise ModelError("Unknown child group")
        for id_ in config.get("remove_orphans", []):
            if (
                id_ not in groups
                or id_ == group_id
                or self.hass.config_entries.async_get_entry(id_) is not None
            ):
                raise ModelError("Only retained orphan records can be removed here")
            if any(leaf.parent == id_ for leaf in leaves.values()) or any(
                g.parent == id_ for g in groups.values()
            ):
                raise ModelError("Move an orphan's direct lights and children before removing it")
            groups.pop(id_)
        runtime = self.snapshot.runtime.copy()
        runtime.masters.setdefault(group_id, 255)
        runtime.masters = {id_: v for id_, v in runtime.masters.items() if id_ in groups}
        runtime.targets = {id_: v for id_, v in runtime.targets.items() if id_ in leaves}
        candidate = Snapshot(
            Topology(groups.values(), leaves.values()),
            runtime,
            dict(self.snapshot.presets if presets is None else presets),
            self.snapshot.config_revision + 1,
            self.snapshot.state_revision + 1,
            {id_: v for id_, v in self.snapshot.capabilities.items() if id_ in leaves},
            dict(self.snapshot.collections),
        )
        candidate.tree.validate_routing(
            {
                id_: resolve_identity(self.hass, leaf.identity)
                for id_, leaf in candidate.tree.leaves.items()
            }
        )
        return candidate

    async def execute(
        self,
        operation: str,
        context: Context,
        *,
        groups=(),
        master=None,
        preset_id=None,
        targets=(),
        fields=None,
        response=False,
    ) -> dict:
        async with self.lock:
            if self.repo.journal:
                raise HomeAssistantError(
                    "Configuration recovery is required before lighting control"
                )
            try:
                await self.flush()
                observations = self.observations()
                state = self.snapshot.runtime
                if operation == "preset":
                    result, effective = preset_plan(
                        self.snapshot.tree,
                        state,
                        observations,
                        self.snapshot.presets[preset_id],
                        collections=self.snapshot.collections,
                    )
                elif operation == "control":
                    effective = resolve_preset(
                        self.snapshot.tree,
                        Preset("batch", "Batch", tuple(targets)),
                        inheritance=False,
                        collections=self.snapshot.collections,
                    )
                    result = absolute(state, observations, effective.values)
                elif operation == "master":
                    selected = tuple(dict.fromkeys(groups))
                    for i, group in enumerate(selected):
                        for other in selected[i + 1 :]:
                            if group in self.snapshot.tree.ancestors(other) or (
                                other in self.snapshot.tree.ancestors(group)
                            ):
                                raise ModelError("Overlapping relative master targets")
                    leaf_sets = [set(self.snapshot.tree.resolve(g)) for g in selected]
                    for i, first in enumerate(leaf_sets):
                        if any(first & other for other in leaf_sets[i + 1 :]):
                            raise ModelError("Overlapping relative master targets")
                    result = Plan(state.copy())
                    for group in selected:
                        part = relative(
                            self.snapshot.tree, result.state, observations, group, master
                        )
                        result.state = part.state
                        result.commands.update(part.commands)
                        result.skipped.update(part.skipped)
                else:
                    group = tuple(groups)[0]
                    result = power(
                        self.snapshot.tree,
                        state,
                        observations,
                        group,
                        operation == "on",
                        master,
                        bool(fields),
                    )
                    if fields and operation == "on":
                        ids = self.snapshot.tree.resolve(group)
                        optional = absolute(
                            state,
                            observations,
                            {id_: dict(fields) | {"power": "on"} for id_ in ids},
                        )
                        for id_, command in optional.commands.items():
                            base = result.commands.get(id_)
                            result.commands[id_] = Intention(
                                id_,
                                True,
                                (dict(base.fields) if base else {}) | dict(command.fields),
                            )
                        result.skipped.update(optional.skipped)
                        result.clamps.update(optional.clamps)
                await self.repo.save(self._with_runtime(result.state))
            except (ModelError, KeyError, OSError) as err:
                raise HomeAssistantError(str(err)) from err
            routes = self.routes()
            self.failures = {}
            self.outcome = {
                "operation_id": uuid4().hex,
                "status": "dispatching",
                "skipped": result.skipped,
                "clamps": result.clamps,
                "attempted": len(result.commands),
            }
            if operation in ("preset", "control"):
                self.outcome["effective"] = {
                    id_: {
                        "properties": encode_properties(values),
                        "sources": {
                            key: {"kind": source.kind, "id": source.id, "depth": source.depth}
                            for key, source in effective.sources[id_].items()
                        },
                    }
                    for id_, values in effective.values.items()
                }
            self.notify()
            semaphore = asyncio.Semaphore(8)

            async def send(command):
                async with semaphore:
                    return await self._send(command, routes[command.leaf_id], observations, context)

            try:
                results = await asyncio.gather(
                    *(send(c) for c in result.commands.values()), return_exceptions=True
                )
            except asyncio.CancelledError:
                self.outcome["status"] = "cancelled_uncertain"
                self.notify()
                raise
            acknowledgements = {}
            for id_, value in zip(result.commands, results, strict=True):
                if isinstance(value, BaseException):
                    self.failures[routes[id_] or id_] = str(value) or type(value).__name__
                else:
                    acknowledgements[id_] = value
            self.outcome.update(
                status="partial" if self.failures else "success",
                failed=dict(self.failures),
                acknowledgements=acknowledgements,
                succeeded=len(result.commands) - len(self.failures),
            )
            try:
                await self.flush()
            except Exception as err:
                self.outcome.update(status="storage_failed_after_dispatch", error=str(err))
                self.notify()
                raise HomeAssistantError("Devices may have changed; state storage failed") from err
            self.notify()
            on_noop = operation == "on" and any(
                observations[id_].available for id_ in self.snapshot.tree.resolve(tuple(groups)[0])
            )
            if not result.commands and operation != "master" and not on_noop:
                self.outcome["status"] = "no_applicable_output"
                self.notify()
                raise HomeAssistantError("No available leaves support the requested output")
            if self.failures and not response:
                raise HomeAssistantError(
                    "J'ai Light Manager could not confirm: " + ", ".join(self.failures)
                )
            return dict(self.outcome)

    def render(self, command: Intention, observation: Observation) -> dict:
        fields = dict(command.fields)
        if color := fields.pop("color", None):
            mode = fields.pop("color_mode")
            if color.kind == "hs":
                rgb = color_util.color_hs_to_RGB(*color.value)
            elif color.kind == "xy":
                rgb = color_util.color_xy_to_RGB(*color.value)
            else:
                rgb = tuple(round(v * 255 / max(color.value)) for v in color.value)
            if mode == "hs":
                value = color.value if color.kind == "hs" else color_util.color_RGB_to_hs(*rgb)
            elif mode == "xy":
                value = color.value if color.kind == "xy" else color_util.color_RGB_to_xy(*rgb)
            elif mode == "rgbw":
                value = color_util.color_rgb_to_rgbw(*rgb)
            elif mode == "rgbww":
                value = color_util.color_rgb_to_rgbww(
                    *rgb, observation.capabilities.min_kelvin, observation.capabilities.max_kelvin
                )
            else:
                value = rgb
            fields[f"{mode}_color"] = value
        return fields

    async def _send(self, command: Intention, route: str | None, observations, context) -> str:
        if route is None or self.routes().get(command.leaf_id) != route:
            raise HomeAssistantError("Physical identity changed before dispatch")
        before = self.hass.states.get(route)
        fields = self.render(command, observations[command.leaf_id])
        acknowledged = asyncio.Event()
        exact = True

        def match_fields(state):
            nonlocal exact
            if state is None or state.state != (STATE_ON if command.on else STATE_OFF):
                return False
            for key, value in fields.items():
                actual = state.attributes.get(key)
                if key.endswith("_color") and (
                    not isinstance(actual, (tuple, list))
                    or len(actual) != len(value)
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in actual)
                ):
                    return False
                if key == "brightness":
                    if type(actual) is not int or not 1 <= actual <= 255:
                        return False
                    if actual != value:
                        if before is None or (state.state, actual) == (
                            before.state,
                            before.attributes.get(key),
                        ):
                            return False
                        exact = False
                elif key == "color_temp_kelvin":
                    if type(actual) is not int or abs(actual - value) > 1:
                        return False
                elif key == "hs_color":
                    if (
                        actual is None
                        or min(abs(actual[0] - value[0]), 360 - abs(actual[0] - value[0])) > 1
                        or (abs(actual[1] - value[1]) > 1)
                    ):
                        return False
                elif key.endswith("_color"):
                    if actual is None:
                        return False
                    if tuple(actual) == tuple(value):
                        continue
                    actual_hs = state.attributes.get("hs_color")
                    if key == "xy_color":
                        rgb = color_util.color_xy_to_RGB(*value)
                    elif key == "rgbw_color":
                        rgb = color_util.color_rgbw_to_rgb(*value)
                    elif key == "rgbww_color":
                        caps = observations[command.leaf_id].capabilities
                        rgb = color_util.color_rgbww_to_rgb(
                            *value, caps.min_kelvin, caps.max_kelvin
                        )
                    else:
                        rgb = value
                    target_hs = color_util.color_RGB_to_hs(*rgb)
                    if (
                        actual_hs is None
                        or min(
                            abs(actual_hs[0] - target_hs[0]), 360 - abs(actual_hs[0] - target_hs[0])
                        )
                        > 1
                        or (abs(actual_hs[1] - target_hs[1]) > 1)
                    ):
                        return False
                elif actual != value:
                    return False
            return True

        def matches(state):
            try:
                return match_fields(state)
            except ValueError, TypeError, IndexError:
                return False  # Malformed reports do not acknowledge an intention.

        @callback
        def changed(event):
            if matches(event.data.get("new_state")):
                acknowledged.set()

        unsubscribe = async_track_state_change_event(self.hass, [route], changed)
        try:
            try:
                await asyncio.wait_for(
                    self.hass.services.async_call(
                        "light",
                        "turn_on" if command.on else "turn_off",
                        {"entity_id": route} | fields,
                        blocking=True,
                        context=context,
                    ),
                    SERVICE_TIMEOUT,
                )
            except TimeoutError as err:
                raise HomeAssistantError("service_timeout") from err
            if matches(self.hass.states.get(route)):
                acknowledged.set()
            try:
                await asyncio.wait_for(acknowledged.wait(), STATE_TIMEOUT)
            except TimeoutError as err:
                raise HomeAssistantError("state_timeout_unconfirmed") from err
            return "exact" if exact else "changed_brightness_report"
        finally:
            unsubscribe()


async def get_manager(hass) -> Manager:
    manager = hass.data.get(DOMAIN)
    if not isinstance(manager, Manager):
        manager = hass.data[DOMAIN] = Manager(hass)
    await manager.load()
    return manager
