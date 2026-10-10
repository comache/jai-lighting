"""Relative fader behavior, including each requested state-model scenario."""

import pytest

from custom_components.jai_lighting.engine import FaderEngine, Member


def state(levels, *, on=True):
    return {f"light.l{i}": Member(True, on, True, value) for i, value in enumerate(levels)}


def engine(levels, master=100):
    states = state(levels)
    group = FaderEngine(list(states))
    group.master = master
    return group, states


def apply(states, commands):
    updated = dict(states)
    for command in commands:
        old = updated[command.entity_id]
        updated[command.entity_id] = Member(
            True,
            command.on,
            old.dimmable,
            command.brightness if command.brightness is not None else old.brightness,
        )
    return updated


def values(states):
    return [member.brightness for member in states.values()]


@pytest.mark.parametrize("target,expected", [(120, [120, 72, 36]), (50, [50, 30, 15])])
def test_proportional_increase_and_decrease(target, expected):
    group, states = engine([100, 60, 30])
    assert values(apply(states, group.set_master(target, states))) == expected


def test_clipping_destroys_above_max_values():
    group, states = engine([255, 153], master=100)
    states = apply(states, group.set_master(120, states))
    assert values(states) == [255, 184]
    states = apply(states, group.set_master(60, states))
    assert values(states) == [128, 92]
    states = apply(states, group.set_master(180, states))
    assert values(states) == [255, 255]
    states = apply(states, group.set_master(100, states))
    assert values(states) == [142, 142]
    assert all(1 <= value <= 255 for value in group.memory.values())


def test_manual_member_adjustment_is_new_baseline():
    group, states = engine([20, 40, 70])
    states["light.l0"] = Member(True, True, True, 80)
    assert values(apply(states, group.set_master(150, states))) == [120, 60, 105]


def test_off_on_without_movement_and_repeated_off():
    group, states = engine([76, 128, 255], master=179)
    off = apply(states, group.turn_off(states))
    group.turn_off(off)
    assert group.master == 179
    assert values(apply(off, group.turn_on(off))) == [76, 128, 255]


def test_off_master_changed_then_on():
    group, states = engine([76, 128, 255], master=180)
    off = apply(states, group.turn_off(states))
    assert group.set_master(90, off) == []
    assert values(apply(off, group.turn_on(off))) == [38, 64, 128]


def test_multiple_off_master_movements_clip_at_each_step():
    group, states = engine([100, 200], master=100)
    off = apply(states, group.turn_off(states))
    group.set_master(200, off)
    assert group.memory == {"light.l0": 200, "light.l1": 255}
    group.set_master(100, off)
    assert values(apply(off, group.turn_on(off))) == [100, 128]


def test_unavailable_member_is_skipped():
    group, states = engine([100, 200])
    states["light.l1"] = Member(False, True, True, 200)
    commands = group.set_master(150, states)
    assert [command.entity_id for command in commands] == ["light.l0"]


def test_removed_replaced_members_prune_memory():
    old, states = engine([80, 160])
    old.turn_off(states)
    new = FaderEngine(["light.l0", "light.replacement"])
    new.restore(old.as_dict())
    assert new.memory == {"light.l0": 80}
    states = {
        "light.l0": Member(True, False, True),
        "light.replacement": Member(True, False, True, 45),
    }
    assert [command.brightness for command in new.turn_on(states)] == [80, 45]


def test_restart_restores_off_targets_and_reconciles_live_on_values():
    old, states = engine([80, 160])
    off = apply(states, old.turn_off(states))
    old.set_master(50, off)
    new = FaderEngine(list(states))
    new.restore(old.as_dict())
    assert new.master == 50
    assert [command.brightness for command in new.turn_on(off)] == [40, 80]
    off["light.l0"] = Member(True, True, True, 60)
    new.observe(off)
    assert new.memory["light.l0"] == 60


def test_mixed_brightness_capabilities_and_missing_on_level():
    states = {
        "light.dim": Member(True, True, True, 100),
        "light.binary": Member(True, True),
        "light.missing": Member(True, True, True),
    }
    group = FaderEngine(list(states))
    group.master = 100
    commands = group.set_master(150, states)
    assert [(c.entity_id, c.brightness) for c in commands] == [("light.dim", 150)]
    off = apply(states, group.turn_off(states))
    commands = group.turn_on(off)
    assert next(c for c in commands if c.entity_id == "light.binary").brightness is None


def test_individually_off_member_stays_off_during_active_master_change():
    group, states = engine([100, 100])
    states["light.l1"] = Member(True, False, True, 100)
    assert [c.entity_id for c in group.set_master(150, states)] == ["light.l0"]


def test_first_on_and_minimum_master():
    group = FaderEngine(["light.new"])
    states = {"light.new": Member(True, False, True)}
    group.set_master(0, states)
    assert group.master == 3
    assert group.turn_on(states)[0].brightness == 3


def test_same_master_is_noop_and_duplicate_leaves_are_deduplicated():
    group = FaderEngine(["light.l0", "light.l0"])
    group.master = 100
    states = state([80])
    assert group.set_master(100, states) == []
    assert len(group.turn_off(states)) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"version": 99, "master": 90},
        {"version": 1, "master": 0, "memory": {"light.l0": 256}},
        {"version": 1, "master": "90", "memory": []},
    ],
)
def test_invalid_restore_data_is_safe(data):
    group, states = engine([80], master=255)
    group.restore(data)
    assert group.master == 255
    assert group.memory == {}
