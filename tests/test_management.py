"""Management preserves the real HA engine, identities, transactions and Store."""

from dataclasses import replace

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from custom_components.jai_lighting.collections import Collection
from custom_components.jai_lighting.management import apply, build_candidate, selected_lights
from custom_components.jai_lighting.manager import get_manager
from custom_components.jai_lighting.models import Group, Identity, Leaf, ModelError
from custom_components.jai_lighting.presets import Preset, Target, resolve
from custom_components.jai_lighting.storage import Repository, Snapshot
from custom_components.jai_lighting.topology import Topology


@pytest.fixture
def snapshot(hass):
    for name in ("one", "two", "three"):
        hass.states.async_set(
            f"light.test_{name}", "off", {"supported_color_modes": ["brightness"]}
        )
    return Snapshot(
        Topology(
            [Group("root", "Root"), Group("child", "Child", "root"), Group("other", "Other")],
            [
                Leaf("one", Identity("entity_id_fallback", "light.test_one"), "child"),
                Leaf("two", Identity("entity_id_fallback", "light.test_two"), "other"),
            ],
        )
    )


def test_move_subtree_rename_and_reorder(hass, snapshot):
    moved = build_candidate(
        hass,
        snapshot,
        [
            {"action": "move", "items": [{"kind": "group", "id": "child"}], "parent": "other"},
            {"action": "update_group", "id": "child", "name": "Renamed"},
            {"action": "reorder", "kind": "group", "parent": None, "ids": ["other", "root"]},
        ],
    )
    assert moved.tree.groups["child"].parent == "other"
    assert moved.tree.resolve("child") == ("one",)
    assert moved.tree.groups["child"].name == "Renamed"
    assert moved.tree.children[None] == ("other", "root")
    assert snapshot.tree.groups["child"].name == "Child"
    assert moved.tree.leaves["one"].identity == snapshot.tree.leaves["one"].identity


@pytest.mark.parametrize(
    "operations",
    [
        [{"action": "move", "items": [{"kind": "group", "id": "root"}], "parent": "child"}],
        [{"action": "move", "items": [{"kind": "leaf", "id": "one"}], "parent": "missing"}],
        [{"action": "reorder", "kind": "leaf", "parent": "child", "ids": ["one", "one"]}],
        [
            {
                "action": "add_lights",
                "parent": "root",
                "entity_ids": ["light.test_one", "light.test_one"],
            }
        ],
        [{"action": "add_lights", "parent": "root", "entity_ids": ["light.test_one"]}],
        [
            {
                "action": "move",
                "parent": None,
                "items": [{"kind": "group", "id": "root"}, {"kind": "group", "id": "child"}],
            }
        ],
        [{"action": "delete_group", "id": "root"}],
    ],
)
def test_invalid_edits_reject_entire_draft(hass, snapshot, operations):
    original = snapshot.dump()
    with pytest.raises(ModelError):
        build_candidate(hass, snapshot, operations)
    assert snapshot.dump() == original


def test_move_lights_remove_and_order(hass, snapshot):
    moved = build_candidate(
        hass,
        snapshot,
        [
            {
                "action": "add_lights",
                "parent": "other",
                "entity_ids": ["light.test_one"],
                "confirm_moves": True,
            },
            {"action": "reorder", "kind": "leaf", "parent": "other", "ids": ["one", "two"]},
        ],
    )
    assert moved.tree.members["other"] == ("one", "two")
    removed = build_candidate(hass, moved, [{"action": "remove_lights", "ids": ["one"]}])
    assert removed.tree.leaves["one"].parent is None
    assert removed.tree.resolve("other") == ("two",)
    assert removed.tree.leaf_path("one") == ("one",)


def test_delete_promotes_contents_and_blocks_dangling_presets(hass, snapshot):
    deleted = build_candidate(
        hass,
        snapshot,
        [{"action": "delete_group", "id": "child", "destination": "root", "confirm": True}],
    )
    assert deleted.tree.leaves["one"].parent == "root"
    with_preset = replace(
        snapshot,
        presets={"p": Preset("p", "Preset", (Target("group", "child", {"power": "off"}),))},
    )
    with pytest.raises(ModelError, match="Unknown group"):
        build_candidate(
            hass, with_preset, [{"action": "delete_group", "id": "child", "confirm": True}]
        )


async def test_label_selection_is_explicit_and_individual_exceptions_survive(hass):
    label = lr.async_get(hass).async_create("Test Label")
    area = ar.async_get(hass).async_create("Test Area")
    registry = er.async_get(hass)
    first = registry.async_get_or_create("light", "test", "one", suggested_object_id="one")
    second = registry.async_get_or_create("light", "test", "two", suggested_object_id="two")
    for entry in (first, second):
        registry.async_update_entity(entry.entity_id, labels={label.label_id}, area_id=area.id)
        hass.states.async_set(entry.entity_id, "off")
    assert selected_lights(hass, {"labels": [label.label_id], "area_id": area.id}) == [
        first.entity_id,
        second.entity_id,
    ]
    snapshot = Snapshot(Topology([Group("g", "Group")]))
    selected = build_candidate(
        hass,
        snapshot,
        [
            {
                "action": "add_lights",
                "parent": "g",
                "entity_ids": selected_lights(hass, {"labels": [label.label_id]}),
            }
        ],
    )
    one = next(leaf.id for leaf in selected.tree.leaves.values() if leaf.identity.value == first.id)
    selected = build_candidate(hass, selected, [{"action": "remove_lights", "ids": [one]}])
    registry.async_update_entity(second.entity_id, labels=set())
    assert len(selected.tree.resolve("g")) == 1
    assert selected_lights(hass, {"labels": [label.label_id]}) == [first.entity_id]


def test_cross_hierarchy_collections_unassigned_and_override_precedence(hass, snapshot):
    combined = build_candidate(
        hass,
        snapshot,
        [
            {
                "action": "update_collection",
                "id": "c",
                "name": "Cross branch",
                "entity_ids": ["light.test_one", "light.test_two", "light.test_three"],
            }
        ],
    )
    assert combined.tree.leaves["one"].parent == "child"
    assert combined.tree.leaves["two"].parent == "other"
    third = combined.collections["c"].members[-1]
    assert combined.tree.leaves[third].parent is None
    recipe = Preset(
        "p",
        "Overrides",
        (
            Target("group", "root", {"brightness_pct": 20}),
            Target("collection", "c", {"brightness_pct": 40}),
            Target("leaf", "one", {"brightness_pct": 60}),
        ),
    )
    result = resolve(combined.tree, recipe, collections=combined.collections)
    assert result.values["one"]["brightness_pct"] == 60
    assert result.values["two"]["brightness_pct"] == 40
    assert result.sources["two"]["brightness_pct"].kind == "collection"
    assert result == resolve(
        combined.tree,
        replace(recipe, targets=tuple(reversed(recipe.targets))),
        collections=combined.collections,
    )


def test_overlapping_collection_conflicts_and_deduplication(snapshot):
    collections = {"a": Collection("a", "A", ("one", "two")), "b": Collection("b", "B", ("one",))}
    recipe = Preset(
        "p",
        "Preset",
        (
            Target("collection", "a", {"brightness_pct": 30}),
            Target("collection", "b", {"brightness_pct": 40}),
            Target("leaf", "one", {"brightness_pct": 90}),
        ),
    )
    with pytest.raises(ModelError, match="overlapping collections"):
        resolve(snapshot.tree, recipe, collections=collections)
    recipe = replace(
        recipe, targets=(recipe.targets[0], Target("collection", "b", {"brightness_pct": 30}))
    )
    result = resolve(snapshot.tree, recipe, collections=collections)
    assert len(result.values) == 2
    assert result == resolve(
        snapshot.tree,
        replace(recipe, targets=tuple(reversed(recipe.targets))),
        collections=collections,
    )


async def test_collection_journal_restart_old_store_and_concurrency(hass, snapshot):
    manager = await get_manager(hass)
    await manager.repo.save(snapshot)
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, calls.append)
    await apply(
        manager,
        0,
        [
            {
                "action": "update_collection",
                "id": "c",
                "name": "Collection",
                "members": ["one", "two"],
            }
        ],
    )
    with pytest.raises(ModelError, match="changed"):
        await apply(manager, 0, [{"action": "update_group", "id": "root", "name": "Stale"}])
    reloaded = Repository(hass)
    await reloaded.load()
    assert reloaded.snapshot.collections["c"].members == ("one", "two")
    old = snapshot.dump()
    del old["collections"]
    assert Snapshot.load(old).collections == {}
    assert Snapshot.load(old).tree.groups == snapshot.tree.groups
    assert not [event for event in calls if event.data["domain"] == "light"]


async def test_collection_interruption_preserves_old_and_new_recipes(hass, snapshot):
    candidate = build_candidate(
        hass,
        snapshot,
        [
            {
                "action": "update_collection",
                "id": "c",
                "name": "Collection",
                "members": ["one", "two"],
            }
        ],
    )
    candidate = replace(
        candidate,
        presets={"p": Preset("p", "Recipe", (Target("collection", "c", {"power": "off"}),))},
    )
    repository = Repository(hass)
    await repository.save(snapshot)
    await repository.prepare(candidate, [])
    restarted = Repository(hass)
    await restarted.load()
    assert restarted.snapshot.collections == {}
    assert restarted.journal["before"]["groups"] == snapshot.dump()["groups"]
    await restarted.finish()
    assert restarted.snapshot.dump() == candidate.dump()
    with pytest.raises(ModelError, match="Missing preset collection"):
        build_candidate(
            hass, restarted.snapshot, [{"action": "delete_collection", "id": "c", "confirm": True}]
        )


async def test_failed_management_save_does_not_publish(hass, snapshot, monkeypatch):
    manager = await get_manager(hass)
    await manager.repo.save(snapshot)

    async def fail(_data):
        raise OSError("Test disk failure")

    monkeypatch.setattr(manager.repo.store, "async_save", fail)
    with pytest.raises(OSError):
        await apply(
            manager,
            0,
            [{"action": "update_collection", "id": "c", "name": "Collection", "members": ["one"]}],
        )
    assert manager.snapshot.dump() == snapshot.dump()
    assert manager.repo.journal is None


async def test_native_review_draft_does_not_revert_later_runtime(hass, snapshot):
    manager = await get_manager(hass)
    await manager.repo.save(snapshot)
    candidate = manager.group_candidate(
        "root", {"name": "Renamed", "members": [], "children": ["child"]}
    )
    current = manager.snapshot.runtime.copy()
    current.masters["root"] = 100
    current.targets["one"] = 80
    await manager.repo.save(replace(manager.snapshot, runtime=current))
    await manager.commit(candidate, [], expected=0)
    assert manager.snapshot.runtime.masters["root"] == 100
    assert manager.snapshot.runtime.targets["one"] == 80


def test_legacy_migration_preserves_existing_collections(snapshot):
    from custom_components.jai_lighting.migration import preflight

    base = replace(snapshot, collections={"c": Collection("c", "Existing", ("one",))})
    migrated = preflight(
        [{"entry_id": "legacy", "data": {"name": "Legacy", "members": []}, "options": {}}],
        {},
        {},
        baseline=base,
    )
    assert migrated.collections == base.collections
    assert migrated.tree.leaves == base.tree.leaves


def test_invalid_stored_collection_is_not_defaulted(snapshot):
    data = snapshot.dump()
    data["collections"] = {"c": {"name": "Malformed", "members": "one"}}
    with pytest.raises(ModelError, match="membership"):
        Snapshot.load(data)


def test_collection_can_retain_missing_registry_member_and_add_lights(hass, snapshot):
    registry = er.async_get(hass)
    record = registry.async_get_or_create("light", "test", "missing", suggested_object_id="missing")
    missing = Leaf("missing", Identity("registry", record.id), None)
    base = replace(
        snapshot,
        tree=Topology(snapshot.tree.groups.values(), [*snapshot.tree.leaves.values(), missing]),
        collections={"c": Collection("c", "Collection", ("missing",))},
    )
    registry.async_remove(record.entity_id)
    updated = build_candidate(
        hass,
        base,
        [
            {
                "action": "update_collection",
                "id": "c",
                "members": ["missing"],
                "add_entity_ids": ["light.test_three"],
            }
        ],
    )
    assert updated.collections["c"].members[0] == "missing"
    assert len(updated.collections["c"].members) == 2
    assert updated.tree.leaves["missing"].identity.value == record.id


@pytest.mark.parametrize(
    "operations",
    [
        [{"action": "move", "items": [{"kind": "group", "id": "child"}], "parent": "other"}],
        [{"action": "move", "items": [{"kind": "leaf", "id": "one"}], "parent": "other"}],
        [{"action": "remove_lights", "ids": ["one", "two"]}],
        [{"action": "update_group", "id": "child", "name": "Updated", "policy": "FULL"}],
        [{"action": "reorder", "kind": "group", "parent": None, "ids": ["other", "root"]}],
    ],
)
async def test_validated_undo_restores_positions_names_and_runtime(hass, snapshot, operations):
    manager = await get_manager(hass)
    snapshot.runtime.masters.update(root=80, child=120)
    snapshot.runtime.targets["one"] = 60
    await manager.repo.save(snapshot)
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, calls.append)
    result = await apply(manager, 0, operations, with_undo=True)
    assert result["revision"] == 1
    assert result["undo_operations"]
    # A later physical baseline is retained, rather than restored from an old UI copy.
    runtime = manager.snapshot.runtime.copy()
    runtime.masters["child"] = 140
    runtime.targets["one"] = 75
    await manager.repo.save(replace(manager.snapshot, runtime=runtime))
    await apply(manager, 1, result["undo_operations"])
    assert manager.snapshot.tree.groups == snapshot.tree.groups
    assert manager.snapshot.tree.leaves == snapshot.tree.leaves
    assert manager.snapshot.runtime.masters["child"] == 140
    assert manager.snapshot.runtime.targets["one"] == 75
    assert not [event for event in calls if event.data["domain"] == "light"]
    reloaded = Repository(hass)
    await reloaded.load()
    assert reloaded.snapshot.tree.groups == snapshot.tree.groups
    assert reloaded.snapshot.tree.leaves == snapshot.tree.leaves


async def test_undo_new_assignment_retains_stable_physical_identity(hass, snapshot):
    manager = await get_manager(hass)
    await manager.repo.save(snapshot)
    result = await apply(
        manager,
        0,
        [{"action": "add_lights", "parent": "child", "entity_ids": ["light.test_three"]}],
        with_undo=True,
    )
    added = next(id_ for id_ in manager.snapshot.tree.leaves if id_ not in snapshot.tree.leaves)
    identity = manager.snapshot.tree.leaves[added].identity
    await apply(manager, result["revision"], result["undo_operations"])
    assert manager.snapshot.tree.leaves[added].parent is None
    assert manager.snapshot.tree.leaves[added].identity == identity
    assert manager.snapshot.tree.resolve("child") == snapshot.tree.resolve("child")
    assert manager.snapshot.runtime.masters == snapshot.runtime.masters


async def test_undo_rejects_other_administrators_revision(hass, snapshot):
    manager = await get_manager(hass)
    await manager.repo.save(snapshot)
    result = await apply(manager, 0, [{"action": "remove_lights", "ids": ["one"]}], with_undo=True)
    await apply(
        manager, 1, [{"action": "update_group", "id": "other", "name": "Another administrator"}]
    )
    current = manager.snapshot.dump()
    with pytest.raises(ModelError, match="changed"):
        await apply(manager, result["revision"], result["undo_operations"])
    assert manager.snapshot.dump() == current


def test_restore_positions_still_validates_cycles_and_parent_identity(hass, snapshot):
    for parent in ("child", "missing"):
        with pytest.raises(ModelError):
            build_candidate(
                hass,
                snapshot,
                [
                    {
                        "action": "restore_positions",
                        "positions": [
                            {"kind": "group", "id": "root", "parent": parent, "order": 0}
                        ],
                    }
                ],
            )
    with pytest.raises(ModelError, match="distinct"):
        build_candidate(hass, snapshot, [{"action": "restore_positions", "positions": []}])


async def test_failed_undo_does_not_publish_or_send_lighting(hass, snapshot, monkeypatch):
    manager = await get_manager(hass)
    await manager.repo.save(snapshot)
    result = await apply(manager, 0, [{"action": "remove_lights", "ids": ["one"]}], with_undo=True)
    before = manager.snapshot.dump()
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, calls.append)

    async def fail(_data):
        raise OSError("Disk unavailable during Undo")

    monkeypatch.setattr(manager.repo.store, "async_save", fail)
    with pytest.raises(OSError):
        await apply(manager, 1, result["undo_operations"])
    assert manager.snapshot.dump() == before
    assert manager.repo.journal is None
    assert not [event for event in calls if event.data["domain"] == "light"]
