"""Offline, one-time native Lovelace generator; never connects to Home Assistant.

Inputs are an actual get_topology snapshot, verified entity bindings, and an
explicit presentation plan. Owner edits are preserved by exclusive output creation.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


def entity(value, domain):
    """Reject absent or guessed bindings instead of producing broken controls."""
    if not isinstance(value, str) or not re.fullmatch(rf"{domain}\.[a-z0-9_]+", value):
        raise ValueError(f"Expected a verified {domain} entity binding")
    return value


def popup_hash(group_id):
    """Keep links stable through renames and reparenting."""
    return "#jai-group-" + hashlib.sha256(group_id.encode()).hexdigest()[:20]


def heading(title, icon="mdi:lightbulb-group-outline"):
    return {"type": "heading", "heading": title, "icon": icon}


def note(text):
    return {"type": "markdown", "content": text}


def action(service, targets, data=None):
    return {
        "action": "perform-action",
        "perform_action": service,
        "target": {"entity_id": targets},
        "data": data or {},
    }


def generate(snapshot, bindings, plan):
    """Build editable Sections views with Bubble controls and topology popups.

    bindings: groups/leaves/masters/presets maps of stable IDs to real entity IDs.
    plan: title, views [{id,name,groups,color}], sidebar [group IDs], optional
    spotify entity and quick_switches [{entity_id,name}]. Placement is independent
    of parentage. Only entities explicitly supplied by discovery are referenced.
    """
    groups, leaves = snapshot["groups"], snapshot["leaves"]
    children = {id_: [] for id_ in groups}
    direct = {id_: [] for id_ in groups}
    for id_, group in groups.items():
        entity(bindings["groups"].get(id_), "light")
        entity(bindings["masters"].get(id_), "number")
        parent = group.get("parent_group_id")
        if parent is not None:
            if parent not in groups:
                raise ValueError("Unknown parent group")
            children[parent].append(id_)
        seen = {id_}
        while parent is not None:
            if parent in seen:
                raise ValueError("Hierarchy cycle")
            seen.add(parent)
            parent = groups[parent].get("parent_group_id")
            if parent is not None and parent not in groups:
                raise ValueError("Unknown ancestor group")
    for id_, leaf in leaves.items():
        entity(bindings["leaves"].get(id_), "light")
        parent = leaf.get("parent_group_id")
        if parent is not None:
            if parent not in groups:
                raise ValueError("Unknown physical light parent")
            direct[parent].append(id_)
    all_lights = list(bindings["groups"].values()) + list(bindings["leaves"].values())
    if len(all_lights) != len(set(all_lights)):
        raise ValueError("Duplicate physical/group entity binding")
    masters = list(bindings["masters"].values())
    if len(masters) != len(set(masters)):
        raise ValueError("A master helper cannot represent two groups")
    for ids in children.values():
        ids.sort(key=lambda id_: (groups[id_].get("sibling_order", 0), groups[id_]["name"]))
    for ids in direct.values():
        ids.sort(key=lambda id_: (leaves[id_].get("sibling_order", 0), id_))

    def group_card(id_, color, quick=False):
        if id_ not in groups:
            raise ValueError("Unknown presentation group")
        source = bindings["groups"][id_]
        buttons = [
            {
                "entity": source,
                "name": "Pornit / Oprit",
                "icon": "mdi:power",
                "show_state": True,
                "tap_action": {"action": "toggle"},
            },
            {
                "name": "Detalii",
                "icon": "mdi:chevron-right",
                "tap_action": {"action": "navigate", "navigation_path": popup_hash(id_)},
            },
        ]
        if quick:
            buttons += [
                {
                    "entity": bindings["groups"][child],
                    "name": groups[child]["name"],
                    "show_name": True,
                    "icon": "mdi:power",
                    "tap_action": {"action": "toggle"},
                }
                for child in children[id_][:2]
            ]
        return {
            "type": "custom:bubble-card",
            "card_type": "button",
            "button_type": "slider",
            "card_layout": "large",
            "rows": 2 if len(buttons) > 2 else 1,
            "grid_options": {"columns": 12, "rows": 2 if len(buttons) > 2 else 1},
            "entity": bindings["masters"][id_],
            "name": groups[id_]["name"],
            "icon": "mdi:tune-vertical",
            "show_state": True,
            "slider_live_update": False,
            "tap_action": {"action": "none"},
            "button_action": {"tap_action": {"action": "none"}},
            "sub_button": {"main": buttons[:2], "bottom": buttons[2:]},
            "styles": (
                "ha-card { --bubble-main-background-color: #302b3d; "
                f"--bubble-accent-color: {color}45; "
                f"--bubble-slider-fill-color: {color}45; "
                "--bubble-border-radius: 24px; --bubble-button-border-radius: 24px; }"
            ),
        }

    def physical_card(id_):
        source = bindings["leaves"][id_]
        capability = snapshot.get("capability_cache", {}).get(id_, {})
        return {
            "type": "custom:bubble-card",
            "card_type": "button",
            "button_type": (
                "slider" if set(capability.get("modes", [])) - {"onoff", "unknown"} else "switch"
            ),
            "entity": source,
            "show_state": True,
            "sub_button": [
                {"icon": "mdi:information-outline", "tap_action": {"action": "more-info"}}
            ],
        }

    def popup(id_, color):
        cards = [group_card(id_, color)]
        if children[id_]:
            cards.append(heading("Grupuri"))
            cards.extend(group_card(child, color) for child in children[id_])
        if direct[id_]:
            cards.append(heading("Lumini directe", "mdi:lightbulb-outline"))
            cards.extend(physical_card(leaf) for leaf in direct[id_])
        if not children[id_] and not direct[id_]:
            cards.append(note("Acest grup nu conține lumini."))
        return {
            "type": "custom:bubble-card",
            "card_type": "pop-up",
            "button_type": "name",
            "hash": popup_hash(id_),
            "name": groups[id_]["name"],
            "icon": "mdi:folder-outline",
            "popup_mode": "adaptive-dialog",
            "show_previous_button": True,
            "cards": cards,
        }

    sidebar = [
        note("## J’ai Bistrot\nLUMINĂ · ATMOSFERĂ · MUZICĂ"),
        {
            "type": "clock",
            "clock_style": "digital",
            "clock_size": "medium",
            "show_seconds": False,
            "time_format": "24",
            "no_background": True,
            "grid_options": {"columns": 12, "rows": 2},
        },
        note("{{ now().strftime('%d.%m.%Y') }}"),
        heading("Control general", "mdi:tune-vertical"),
    ]
    sidebar += [group_card(id_, "#c4b5fd") for id_ in plan.get("sidebar", [])]
    if plan.get("spotify"):
        sidebar += [
            heading("Spotify", "mdi:spotify"),
            {
                "type": "custom:mushroom-media-player-card",
                "entity": entity(plan["spotify"], "media_player"),
                "name": "Spotify",
                "use_media_info": True,
                "show_volume_level": True,
                "media_controls": ["previous", "play_pause_stop", "next"],
                "volume_controls": ["volume_mute", "volume_set"],
                "collapsible_controls": True,
                "layout": "horizontal",
                "grid_options": {"columns": 12, "rows": 2},
            },
        ]
    sidebar.append(heading("Acces rapid", "mdi:flash-outline"))
    for switch in plan.get("quick_switches", []):
        sidebar.append(
            {
                "type": "tile",
                "entity": entity(switch["entity_id"], "switch"),
                "name": switch["name"],
            }
        )
    if not plan.get("quick_switches"):
        sidebar.append(note("Adaugă aici prize sau comutatoare din editorul dashboardului."))
    sidebar += [
        heading("Sistem", "mdi:information-outline"),
        note("Spațiu pentru alerte și senzori."),
    ]

    moods = [heading("Moods", "mdi:palette-outline")]
    for id_, preset in snapshot.get("presets", {}).items():
        source = entity(bindings.get("presets", {}).get(id_), "scene")
        moods.append(
            {
                "type": "button",
                "name": preset["name"],
                "icon": "mdi:palette",
                "tap_action": action("jai_lighting.activate_preset", source),
            }
        )
    if not snapshot.get("presets"):
        moods.append(note("Niciun preset configurat. Creează presetări în J’ai Light Manager."))
    moods.append(heading("Selecții speciale", "mdi:selection-multiple"))
    for collection in snapshot.get("collections", {}).values():
        members = collection["members"]
        sources = []
        for id_ in members:
            source = bindings["groups"].get(id_) or bindings["leaves"].get(id_)
            sources.append(entity(source, "light"))
        moods.append(heading(collection["name"]))
        if not sources:
            moods.append(note("Selecție fără membri."))
            continue
        moods.append(
            {
                "type": "horizontal-stack",
                "cards": [
                    {
                        "type": "button",
                        "name": label,
                        "icon": "mdi:power",
                        "tap_action": action(
                            "jai_lighting.control", sources, {"properties": {"power": power}}
                        ),
                    }
                    for label, power in [("Pornește", "on"), ("Oprește", "off")]
                ],
            }
        )
    if not snapshot.get("collections"):
        moods.append(note("Nicio selecție specială configurată."))

    views = []
    paths = set()
    for view in plan["views"]:
        path = view["id"]
        if not re.fullmatch(r"[a-z0-9_-]+", path) or path in paths:
            raise ValueError("Each view needs a unique safe path")
        paths.add(path)
        color = view.get("color", "#edbfd1")
        if not re.fullmatch(r"#[a-fA-F0-9]{6}", color):
            raise ValueError("Use a six-digit accent color")
        cards = [heading(view["name"])]
        cards += [group_card(id_, color, quick=True) for id_ in view["groups"]]
        if not view["groups"]:
            cards.append(
                note(
                    "Niciun grup plasat aici. Poți adăuga orice grup din editor, "
                    "fără a schimba ierarhia."
                )
            )
        # Standalone Bubble popups own their nested cards and use stable hashes.
        cards.extend(popup(id_, color) for id_ in groups)
        views.append(
            {
                "title": view["name"],
                "path": path,
                "type": "sections",
                "max_columns": 3,
                "dense_section_placement": False,
                "sections": [
                    {
                        "type": "grid",
                        "cards": sidebar,
                        "background": {"color": "#252132", "opacity": 100},
                    },
                    {"type": "grid", "column_span": 2, "cards": cards + moods},
                ],
            }
        )
    if not views:
        raise ValueError("At least one presentation view is required")
    return {"title": plan["title"], "views": views}


def write_new(path, config):
    """Never replace an existing export or the owner's edited dashboard."""
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(config, output, ensure_ascii=False, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "bindings", "plan", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    inputs = [json.loads(path.read_text()) for path in (args.snapshot, args.bindings, args.plan)]
    write_new(args.output, generate(*inputs))


if __name__ == "__main__":
    main()
