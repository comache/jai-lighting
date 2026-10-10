"""Validated primary forest and iterative descendant/path resolution."""

from collections import defaultdict
from collections.abc import Iterable
from types import MappingProxyType

from .models import Group, Leaf, ModelError


class Topology:
    def __init__(self, groups: Iterable[Group] = (), leaves: Iterable[Leaf] = ()) -> None:
        group_list, leaf_list = list(groups), list(leaves)
        if len(group_list) + len(leaf_list) > 10000:
            raise ModelError("Topology exceeds 10,000 records")
        self.groups = MappingProxyType({g.id: g for g in group_list})
        self.leaves = MappingProxyType({leaf.id: leaf for leaf in leaf_list})
        if len(self.groups) != len(group_list) or len(self.leaves) != len(leaf_list):
            raise ModelError("Duplicate topology ID")
        if self.groups.keys() & self.leaves.keys():
            raise ModelError("Group/leaf identity collision")
        children: dict[str | None, list[str]] = defaultdict(list)
        members: dict[str, list[str]] = defaultdict(list)
        identities = set()
        for group in group_list:
            if group.parent is not None and group.parent not in self.groups:
                raise ModelError(f"Missing parent for group {group.id}")
            children[group.parent].append(group.id)
        for leaf in leaf_list:
            if leaf.parent not in self.groups:
                raise ModelError(f"Missing parent for leaf {leaf.id}")
            if leaf.identity.key in identities:
                raise ModelError(f"Duplicate physical identity: {leaf.id}")
            identities.add(leaf.identity.key)
            members[leaf.parent].append(leaf.id)
        # Color marking visits each parent edge once, including disconnected cycles.
        complete: set[str] = set()
        for group in group_list:
            path: set[str] = set()
            current: str | None = group.id
            while current is not None and current not in complete:
                if current in path:
                    raise ModelError(f"Cycle at group {current}")
                path.add(current)
                current = self.groups[current].parent
            complete.update(path)
        self.children = MappingProxyType(
            {
                key: tuple(sorted(ids, key=lambda i: (self.groups[i].order, i)))
                for key, ids in children.items()
            }
        )
        self.members = MappingProxyType(
            {
                key: tuple(sorted(ids, key=lambda i: (self.leaves[i].order, i)))
                for key, ids in members.items()
            }
        )

    def ancestors(self, group_id: str) -> tuple[str, ...]:
        if group_id not in self.groups:
            raise ModelError(f"Unknown group: {group_id}")
        path = []
        current: str | None = group_id
        while current is not None:
            path.append(current)
            current = self.groups[current].parent
        return tuple(reversed(path))

    def leaf_path(self, leaf_id: str) -> tuple[str, ...]:
        if leaf_id not in self.leaves:
            raise ModelError(f"Unknown leaf: {leaf_id}")
        return (*self.ancestors(self.leaves[leaf_id].parent), leaf_id)

    def resolve(self, group_id: str) -> tuple[str, ...]:
        if group_id not in self.groups:
            raise ModelError(f"Unknown group: {group_id}")
        stack, seen, result = [group_id], set(), []
        while stack:
            node = stack.pop()
            if node in seen:
                raise ModelError(f"Duplicate group path: {node}")
            seen.add(node)
            result.extend(self.members.get(node, ()))
            stack.extend(reversed(self.children.get(node, ())))
        if len(result) != len(set(result)):
            raise ModelError("Duplicate physical leaf path")
        return tuple(result)

    def validate_routing(self, resolved: dict[str, str | None]) -> None:
        """Catch registry/fallback aliases without accepting an invalid tree."""
        seen: dict[str, str] = {}
        for leaf_id in self.leaves:
            if entity_id := resolved.get(leaf_id):
                if entity_id in seen:
                    raise ModelError(f"Physical alias collision: {seen[entity_id]}, {leaf_id}")
                seen[entity_id] = leaf_id
