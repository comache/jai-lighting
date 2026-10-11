"""Approved per-property inheritance and post-resolution validation contract."""

from dataclasses import replace

import pytest

from custom_components.jai_lighting.models import (
    Capabilities,
    Group,
    Identity,
    Leaf,
    Observation,
    RuntimeState,
)
from custom_components.jai_lighting.planner import relative
from custom_components.jai_lighting.presets import Preset, PresetError, Target, plan, resolve
from custom_components.jai_lighting.topology import Topology


@pytest.fixture
def tree():
    return Topology(
        [
            Group("r", "Root"),
            Group("a", "A", "r"),
            Group("deep", "Deep", "a"),
            Group("b", "B", "r"),
        ],
        [
            Leaf(i, Identity("registry", i), parent)
            for i, parent in [("x", "a"), ("y", "deep"), ("z", "b"), ("w", "r")]
        ],
    )


def recipe(*targets):
    return Preset("preset", "User recipe", tuple(targets))


def target(id_, **props):
    return Target("group", id_, props)


def test_parent_default_child_override_and_inherited_other_properties(tree):
    preset = recipe(
        target("r", power="on", brightness_pct=30, color_temp_kelvin=2700),
        target("a", brightness_pct=50),
    )
    result = resolve(tree, preset)
    assert result.values["x"] == {"power": "on", "brightness_pct": 50, "color_temp_kelvin": 2700}
    assert result.values["w"]["brightness_pct"] == 30
    assert result.sources["x"]["power"].id == "r"
    assert result.sources["x"]["brightness_pct"].id == "a"
    reversed_result = resolve(tree, replace(preset, targets=tuple(reversed(preset.targets))))
    assert reversed_result == result


def test_multilevel_leaf_and_sibling_overrides(tree):
    result = resolve(
        tree,
        recipe(
            target("r", power="on", brightness_pct=30, color_temp_kelvin=2700),
            target("a", brightness_pct=50),
            target("deep", color_temp_kelvin=2200),
            target("b", brightness_pct=80),
            Target("leaf", "y", {"brightness_pct": 90}),
        ),
    )
    assert result.values["y"] == {"power": "on", "brightness_pct": 90, "color_temp_kelvin": 2200}
    assert result.values["z"]["brightness_pct"] == 80
    assert result.values["x"]["brightness_pct"] == 50
    assert result.sources["y"]["brightness_pct"].kind == "leaf"


def test_identical_duplicates_collapse_and_disjoint_fields_merge(tree):
    result = resolve(
        tree,
        recipe(
            target("r", brightness_pct=30),
            target("r", brightness_pct=30.0),
            target("r", power="on"),
        ),
    )
    assert len(result.values) == 4
    assert result.values["x"] == {"brightness_pct": 30, "power": "on"}


@pytest.mark.parametrize("a,b", [(30, 50), (30, 30.01)])
def test_same_specificity_conflict_even_when_shadowed(tree, a, b):
    with pytest.raises(PresetError, match="Same-specificity"):
        resolve(
            tree,
            recipe(
                target("r", brightness_pct=a),
                target("r", brightness_pct=b),
                target("a", brightness_pct=80),
            ),
        )


@pytest.mark.parametrize(
    "ancestor,child",
    [
        ({"power": "off"}, {"brightness_pct": 50}),
        ({"color_temp_kelvin": 2700}, {"color": {"kind": "hs", "value": [120, 50]}}),
        ({"effect": "pulse"}, {"color_temp_kelvin": 2200}),
    ],
)
def test_final_cross_property_errors_include_provenance(tree, ancestor, child):
    with pytest.raises(PresetError) as error:
        resolve(tree, recipe(target("r", **ancestor), target("a", **child)))
    assert error.value.leaf in {"x", "y"}
    assert len(error.value.values) == 2
    assert {s.id for s in error.value.sources.values()} == {"r", "a"}


def test_on_override_makes_inherited_off_valid(tree):
    result = resolve(
        tree, recipe(target("r", power="off"), target("a", power="on", brightness_pct=50))
    )
    assert result.values["x"] == {"power": "on", "brightness_pct": 50}
    assert result.values["w"] == {"power": "off"}


def test_reparent_changes_inherited_values(tree):
    preset = recipe(
        target("r", brightness_pct=30),
        target("a", brightness_pct=50),
        target("b", brightness_pct=80),
    )
    changed = Topology(
        [replace(g, parent="b") if g.id == "deep" else g for g in tree.groups.values()],
        tree.leaves.values(),
    )
    assert resolve(tree, preset).values["y"]["brightness_pct"] == 50
    assert resolve(changed, preset).values["y"]["brightness_pct"] == 80


def test_leaf_move_changes_inheritance(tree):
    preset = recipe(
        target("r", brightness_pct=30),
        target("a", brightness_pct=50),
        target("b", brightness_pct=80),
    )
    changed = Topology(
        tree.groups.values(),
        [replace(leaf, parent="b") if leaf.id == "x" else leaf for leaf in tree.leaves.values()],
    )
    assert resolve(changed, preset).values["x"]["brightness_pct"] == 80


def test_filter_after_inheritance_no_effect_fallback(tree):
    preset = recipe(
        target("r", power="on", brightness_pct=30, effect="supported"),
        target("a", effect="unsupported"),
    )
    binary = Observation(True, False, capabilities=Capabilities(frozenset({"onoff"})))
    color = Observation(True, True, 90, Capabilities(frozenset({"hs"}), effects=("supported",)))
    result, effective = plan(tree, RuntimeState(), {"x": binary, "y": color}, preset)
    assert effective.values["y"]["effect"] == "unsupported"
    assert "effect" not in result.commands["y"].fields
    assert result.commands["x"].fields == {}
    assert result.commands["x"].on


def test_preset_keeps_every_master_and_next_relative_uses_actual(tree):
    caps = Capabilities(frozenset({"brightness"}))
    original = RuntimeState({id_: 128 for id_ in tree.groups}, {"x": 20})
    result, _ = plan(
        tree,
        original,
        {"x": Observation(True, True, 20, caps)},
        recipe(Target("leaf", "x", {"brightness_pct": 80})),
    )
    assert result.state.masters == original.masters
    assert result.commands["x"].fields["brightness"] == 204
    next_ = relative(tree, result.state, {"x": Observation(True, True, 204, caps)}, "a", 64)
    assert next_.commands["x"].fields["brightness"] == 102


def test_live_batch_does_not_acquire_preset_override_semantics(tree):
    with pytest.raises(PresetError, match="non-preset batch"):
        resolve(
            tree,
            recipe(target("r", brightness_pct=30), target("a", brightness_pct=50)),
            inheritance=False,
        )
