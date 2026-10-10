"""Side-effect-free installation-wide v1 preflight and explicit ownership."""

from uuid import NAMESPACE_URL, uuid5

from .models import Group, Identity, Leaf, ModelError
from .storage import Snapshot
from .topology import Topology


class OwnershipRequired(ModelError):
    def __init__(self, overlaps: dict[str, list[str]], memories: dict) -> None:
        self.overlaps, self.memories = overlaps, memories
        super().__init__("Legacy overlap requires explicit primary-owner selection")


def preflight(
    entries: list[dict],
    restored: dict[str, dict],
    identities: dict[str, Identity],
    owners: dict[str, str] | None = None,
    baseline: Snapshot | None = None,
) -> Snapshot:
    owners = owners or {}
    base = baseline or Snapshot()
    memberships: dict[str, list[str]] = {}
    configs = {}
    for entry in entries:
        config = entry.get("options") or entry["data"]
        configs[entry["entry_id"]] = config
        for entity_id in dict.fromkeys(config["members"]):
            identity = identities.get(entity_id, Identity("entity_id_fallback", entity_id))
            key = f"{identity.kind}:{identity.value}"
            memberships.setdefault(key, []).append(entry["entry_id"])
    unresolved = {
        key: ids for key, ids in memberships.items() if len(ids) > 1 and owners.get(key) not in ids
    }
    if unresolved:
        raise OwnershipRequired(unresolved, restored)
    groups, leaves = list(base.tree.groups.values()), list(base.tree.leaves.values())
    runtime = base.runtime.copy()
    known = {leaf.identity.key for leaf in leaves}
    for group_id, config in configs.items():
        if group_id in base.tree.groups:
            continue
        groups.append(Group(group_id, config["name"]))
        extra = restored.get(group_id, {})
        valid_restore = extra.get("version") == 1
        master = extra.get("master") if valid_restore else None
        runtime.masters[group_id] = master if type(master) is int and 3 <= master <= 255 else 255
        memory = extra.get("memory", {}) if valid_restore else {}
        if not isinstance(memory, dict):
            memory = {}
        for order, entity_id in enumerate(dict.fromkeys(config["members"])):
            identity = identities.get(entity_id, Identity("entity_id_fallback", entity_id))
            key = f"{identity.kind}:{identity.value}"
            owner = owners.get(key, memberships[key][0])
            if owner != group_id:
                continue
            if identity.key in known:
                raise ModelError("Legacy leaf already belongs to the v2 topology")
            known.add(identity.key)
            leaf_id = uuid5(NAMESPACE_URL, f"jai_lighting:{key}").hex
            leaves.append(Leaf(leaf_id, identity, group_id, order))
            brightness = memory.get(entity_id)
            if type(brightness) is int and 1 <= brightness <= 255:
                runtime.targets[leaf_id] = brightness
    result = Snapshot(
        Topology(groups, leaves),
        runtime,
        dict(base.presets),
        base.config_revision + 1,
        base.state_revision + 1,
        dict(base.capabilities),
    )
    result.validate()
    return result
