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
