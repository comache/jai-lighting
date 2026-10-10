"""Versioned HA snapshot and recoverable cross-file configuration journal."""

from dataclasses import dataclass, field
from typing import Any

from homeassistant.helpers.storage import Store

from .capabilities import Color
from .models import Group, Identity, Leaf, ModelError, PowerPolicy, RuntimeState
from .presets import Preset, Target, resolve
from .topology import Topology


def encode_properties(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"kind": value.kind, "value": list(value.value)} if isinstance(value, Color) else value
        for key, value in values.items()
    }


@dataclass
class Snapshot:
    tree: Topology = field(default_factory=Topology)
    runtime: RuntimeState = field(default_factory=RuntimeState)
    presets: dict[str, Preset] = field(default_factory=dict)
    config_revision: int = 0
    state_revision: int = 0
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)

    def validate(self) -> None:
        self.runtime.validate()
        if self.runtime.masters.keys() - self.tree.groups.keys():
            raise ModelError("Unknown group runtime identity")
        if self.runtime.targets.keys() - self.tree.leaves.keys():
            raise ModelError("Unknown leaf runtime identity")
        if self.config_revision < 0 or self.state_revision < 0:
            raise ModelError("Invalid snapshot revision")
        for id_, preset in self.presets.items():
            if id_ != preset.id:
                raise ModelError("Preset identity mismatch")
            resolve(self.tree, preset)

    def dump(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": 2,
            "config_revision": self.config_revision,
            "state_revision": self.state_revision,
            "groups": {
                g.id: {
                    "name": g.name,
                    "parent_group_id": g.parent,
                    "power_policy": g.policy.value,
                    "sibling_order": g.order,
                }
                for g in self.tree.groups.values()
            },
            "leaves": {
                leaf.id: {
                    "identity": {"kind": leaf.identity.kind, "value": leaf.identity.value},
                    "parent_group_id": leaf.parent,
                    "sibling_order": leaf.order,
                }
                for leaf in self.tree.leaves.values()
            },
            "presets": {
                id_: {
                    "name": preset.name,
                    "targets": [
                        {
                            "target": {"kind": t.kind, "id": t.id},
                            "properties": encode_properties(dict(t.properties)),
                        }
                        for t in preset.targets
                    ],
                }
                for id_, preset in self.presets.items()
            },
            "runtime": {
                "group_masters": dict(self.runtime.masters),
                "leaf_brightness_targets": dict(self.runtime.targets),
            },
            "capability_cache": self.capabilities,
        }

    @classmethod
    def load(cls, data: dict[str, Any]) -> Snapshot:
        if data.get("schema_version") != 2:
            raise ModelError("Unsupported snapshot schema; stored data preserved")
        try:
            groups = [
                Group(
                    id_,
                    g["name"],
                    g["parent_group_id"],
                    PowerPolicy(g["power_policy"]),
                    g["sibling_order"],
                )
                for id_, g in data["groups"].items()
            ]
            leaves = [
                Leaf(
                    id_,
                    Identity(record["identity"]["kind"], record["identity"]["value"]),
                    record["parent_group_id"],
                    record["sibling_order"],
                )
                for id_, record in data["leaves"].items()
            ]
            presets = {
                id_: Preset(
                    id_,
                    p["name"],
                    tuple(
                        Target(t["target"]["kind"], t["target"]["id"], t["properties"])
                        for t in p["targets"]
                    ),
                )
                for id_, p in data["presets"].items()
            }
            runtime = data["runtime"]
            result = cls(
                Topology(groups, leaves),
                RuntimeState(
                    dict(runtime["group_masters"]), dict(runtime["leaf_brightness_targets"])
                ),
                presets,
                data["config_revision"],
                data["state_revision"],
                dict(data.get("capability_cache", {})),
            )
            result.validate()
            return result
        except (KeyError, TypeError, ValueError) as err:
            raise ModelError(f"Invalid stored lighting snapshot: {err}") from err


class Repository:
    """Caller serializes writes; publish only after durable save succeeds."""

    def __init__(self, hass, store=None) -> None:
        self.store = store or Store(hass, 2, "jai_lighting.state")
        self.snapshot = Snapshot()
        self.journal: dict | None = None
        self.legacy: dict = {}
        self.failed = False

    async def load(self) -> None:
        if (data := await self.store.async_load()) is not None:
            self.snapshot = Snapshot.load(data)
            self.journal = data.get("journal")
            self.legacy = data.get("legacy_backup", {})
            if self.journal:
                Snapshot.load(self.journal["candidate"])
                Snapshot.load(self.journal["before"])

    async def _write(self, snapshot: Snapshot, journal: dict | None) -> None:
        data = snapshot.dump() | {"journal": journal, "legacy_backup": self.legacy}
        try:
            await self.store.async_save(data)
        except Exception:
            self.failed = True
            raise
        self.failed = False

    async def save(self, snapshot: Snapshot) -> None:
        if self.journal:
            raise ModelError("An unfinished configuration transaction requires recovery")
        await self._write(snapshot, None)
        self.snapshot = snapshot

    async def prepare(
        self,
        candidate: Snapshot,
        hosts: list[dict],
        *,
        kind="edit",
        token: str | None = None,
        legacy: dict | None = None,
    ) -> None:
        if self.journal:
            raise ModelError("Another configuration transaction is pending")
        if legacy is not None:
            self.legacy = legacy
        journal = {
            "kind": kind,
            "token": token,
            "phase": "prepared",
            "before": self.snapshot.dump(),
            "candidate": candidate.dump(),
            "hosts": hosts,
        }
        await self._write(self.snapshot, journal)
        self.journal = journal

    async def finish(self) -> None:
        if not self.journal:
            return
        candidate = Snapshot.load(self.journal["candidate"])
        await self._write(candidate, None)
        self.snapshot, self.journal = candidate, None

    async def abort(self) -> None:
        if self.journal:
            before = Snapshot.load(self.journal["before"])
            await self._write(before, None)
            self.snapshot, self.journal = before, None
