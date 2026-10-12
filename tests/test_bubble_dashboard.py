"""Verify offline dashboard generation without ever contacting physical hardware."""

from copy import deepcopy

import pytest

from scripts.bubble_dashboard import generate, popup_hash, write_new


@pytest.fixture
def inputs():
    return (
        {
            "groups": {
                "root": {"name": "Root", "parent_group_id": None},
                "child": {"name": "Child", "parent_group_id": "root"},
                "empty": {"name": "Empty", "parent_group_id": None},
            },
            "leaves": {
                "lamp": {"parent_group_id": "child"},
                "binary": {"parent_group_id": "root"},
            },
            "capability_cache": {"lamp": {"modes": ["color_temp"]}, "binary": {"modes": ["onoff"]}},
            "presets": {"evening": {"name": "Evening"}},
            "collections": {"mixed": {"name": "Mixed", "members": ["root", "lamp"]}},
        },
        {
            "groups": {"root": "light.root", "child": "light.child", "empty": "light.empty"},
            "masters": {"root": "number.root", "child": "number.child", "empty": "number.empty"},
            "leaves": {"lamp": "light.actual_lamp", "binary": "light.actual_binary"},
            "presets": {"evening": "scene.actual_evening"},
        },
        {
            "title": "Fixture dashboard",
            "sidebar": ["root"],
            "spotify": "media_player.actual_player",
            "views": [
                {"id": "first", "name": "First", "groups": ["child", "root"]},
                {"id": "second", "name": "Second", "groups": ["child"]},
            ],
        },
    )


def cards(value):
    if isinstance(value, dict):
        if "type" in value:
            yield value
        for child in value.values():
            yield from cards(child)
    elif isinstance(value, list):
        for child in value:
            yield from cards(child)


def test_safe_master_bindings_and_separate_power(inputs):
    before = deepcopy(inputs)
    config = generate(*inputs)
    assert inputs == before
    sliders = [c for c in cards(config) if c.get("entity", "").startswith("number.")]
    assert sliders
    for card in sliders:
        assert card["button_type"] == "slider"
        assert card["button_action"]["tap_action"]["action"] == "none"
        power, details = card["sub_button"]["main"]
        assert power["tap_action"] == {"action": "toggle"}
        assert power["entity"] == card["entity"].replace("number.", "light.")
        assert details["tap_action"]["action"] == "navigate"


def test_popup_children_direct_leaves_and_stable_links(inputs):
    config = generate(*inputs)
    popups = {c["hash"]: c for c in cards(config["views"][0]) if c.get("card_type") == "pop-up"}
    assert set(popups) == {popup_hash(i) for i in inputs[0]["groups"]}
    root = list(cards(popups[popup_hash("root")]["cards"]))
    assert any(c.get("entity") == "number.child" for c in root)
    assert any(
        c.get("entity") == "light.actual_binary" and c["button_type"] == "switch" for c in root
    )
    assert not any(c.get("entity") == "light.actual_lamp" for c in root)
    child = list(cards(popups[popup_hash("child")]))
    assert any(
        c.get("entity") == "light.actual_lamp" and c["button_type"] == "slider" for c in child
    )
    inputs[0]["groups"]["child"].update(name="Renamed", parent_group_id=None)
    changed = generate(*inputs)
    assert {c["hash"] for c in cards(changed) if "hash" in c} == set(popups)


def test_placement_is_independent_and_sidebar_is_generated(inputs):
    config = generate(*inputs)
    a, b = config["views"]
    assert a["sections"][0] == b["sections"][0]
    assert a["type"] == "sections" and a["max_columns"] == 3
    for view in (a, b):
        assert view["sections"][1]["cards"][1]["entity"] == "number.child"


def test_real_presets_and_collection_batch_action(inputs):
    config = generate(*inputs)
    actions = [
        c["tap_action"]
        for c in cards(config)
        if "tap_action" in c and "perform_action" in c["tap_action"]
    ]
    preset = next(a for a in actions if a["perform_action"] == "jai_lighting.activate_preset")
    assert preset["target"]["entity_id"] == "scene.actual_evening"
    batch = next(a for a in actions if a["perform_action"] == "jai_lighting.control")
    assert batch["target"]["entity_id"] == ["light.root", "light.actual_lamp"]
    assert batch["data"]["properties"]["power"] in ("on", "off")


def test_empty_sections_do_not_invent_controls(inputs):
    inputs[0]["presets"] = {}
    inputs[0]["collections"] = {}
    inputs[2]["views"][0]["groups"] = []
    config = generate(*inputs)
    assert not any(c.get("tap_action", {}).get("perform_action") for c in cards(config))


@pytest.mark.parametrize(
    "defect",
    ["cycle", "parent", "helper", "duplicate", "route", "placement", "path", "color", "preset"],
)
def test_incomplete_or_ambiguous_discovery_is_rejected(inputs, defect):
    snapshot, bindings, plan = inputs
    if defect == "cycle":
        snapshot["groups"]["root"]["parent_group_id"] = "child"
    elif defect == "parent":
        snapshot["groups"]["child"]["parent_group_id"] = "absent"
    elif defect == "helper":
        bindings["masters"]["child"] = "light.child"
    elif defect == "duplicate":
        bindings["masters"]["child"] = "number.root"
    elif defect == "route":
        del bindings["leaves"]["lamp"]
    elif defect == "placement":
        plan["views"][0]["groups"] = ["absent"]
    elif defect == "path":
        plan["views"][1]["id"] = "first"
    elif defect == "color":
        plan["views"][0]["color"] = "red; bad-css"
    else:
        bindings["presets"] = {}
    with pytest.raises(ValueError):
        generate(*inputs)


def test_regeneration_never_overwrites_owner_configuration(tmp_path):
    destination = tmp_path / "dashboard.json"
    write_new(destination, {"owner": "edits"})
    with pytest.raises(FileExistsError):
        write_new(destination, {"generated": True})
    assert '"owner": "edits"' in destination.read_text()
