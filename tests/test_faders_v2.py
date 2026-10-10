"""Shared bounded targets and independently addressed hierarchy masters."""

import pytest

from custom_components.jai_lighting.models import (
    Capabilities,
    Group,
    Identity,
    Leaf,
    Observation,
    PowerPolicy,
    RuntimeState,
)
from custom_components.jai_lighting.planner import power, relative
from custom_components.jai_lighting.topology import Topology


def tree(policy=PowerPolicy.RESTORE):
    return Topology(
        [Group("r", "Root", policy=policy), Group("a", "Child", "r")],
        [
            Leaf("one", Identity("registry", "one"), "a"),
            Leaf("two", Identity("registry", "two"), "r"),
        ],
    )


def obs(value=153, on=True, available=True, modes=("brightness",)):
    return Observation(available, on, value, Capabilities(frozenset(modes)))


def test_ancestor_then_child_independent_faders_real_baseline():
    state = RuntimeState({"r": 128, "a": 102})
    first = relative(tree(), state, {"one": obs(), "two": obs()}, "r", 192)
    assert first.state.masters == {"r": 192, "a": 102}
    assert first.commands["one"].fields["brightness"] == 230
    second = relative(tree(), first.state, {"one": obs(230), "two": obs(230)}, "a", 51)
    assert second.state.masters == {"r": 192, "a": 51}
    assert second.commands["one"].fields["brightness"] == 115
    assert "two" not in second.commands
    manual = relative(tree(), second.state, {"one": obs(200)}, "a", 102)
    assert manual.commands["one"].fields["brightness"] == 255
    assert state.masters == {"r": 128, "a": 102}


def test_off_shared_targets_ancestor_child_and_sequential_clipping():
    observations = {"one": obs(200, False), "two": obs(100, False)}
    first = relative(tree(), RuntimeState(), observations, "r", 128)
    assert not first.commands
    assert first.state.targets == {"one": 100, "two": 50}
    second = relative(tree(), first.state, observations, "a", 128)
    assert second.state.targets == {"one": 50, "two": 50}
    third = relative(tree(), second.state, observations, "a", 255)
    assert third.state.targets["one"] == 100
    restored = power(tree(), third.state, observations, "a", True)
    assert restored.commands["one"].fields["brightness"] == 100


def test_active_ancestor_leaves_off_child_memory_unchanged():
    state = RuntimeState(targets={"one": 80})
    plan = relative(tree(), state, {"one": obs(200, False), "two": obs(100)}, "r", 128)
    assert plan.state.targets["one"] == 80
    assert set(plan.commands) == {"two"}


def test_full_restore_and_explicit_master_override():
    observations = {"one": obs(70), "two": obs(100, False)}
    full = power(tree(PowerPolicy.FULL), RuntimeState(), observations, "r", True)
    assert {c.fields["brightness"] for c in full.commands.values()} == {255}
    assert not full.state.masters  # Absolute FULL must not move faders.
    restore = power(tree(), RuntimeState(), observations, "r", True)
    assert set(restore.commands) == {"two"}
    assert restore.commands["two"].fields["brightness"] == 100
    explicit = power(tree(PowerPolicy.FULL), RuntimeState(), observations, "r", True, 128)
    assert explicit.commands["one"].fields["brightness"] == 35
    assert explicit.commands["two"].fields["brightness"] == 100


def test_binary_missing_brightness_unavailable_and_repeated_off():
    observations = {"one": obs(None, modes=("onoff",)), "two": obs(None)}
    plan = relative(tree(), RuntimeState(), observations, "r", 128)
    assert not plan.commands
    assert plan.skipped["two"] == ["missing_brightness"]
    off = power(tree(), RuntimeState(targets={"two": 60}), observations, "r", False)
    assert len(off.commands) == 2
    repeated = power(tree(), off.state, {"two": obs(240, False)}, "r", False)
    assert repeated.state.targets["two"] == 60
    missing = relative(tree(), RuntimeState(targets={"one": 200}), {}, "r", 128)
    assert missing.state.targets["one"] == 100
    assert not missing.commands


@pytest.mark.parametrize("master", [0, 2, 256, float("nan")])
def test_invalid_master(master):
    with pytest.raises(ValueError):
        relative(tree(), RuntimeState(), {}, "r", master)
