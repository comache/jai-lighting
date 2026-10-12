"""Build native HA Template Number definitions for Bubble relative masters.

This module only generates configuration. It does not contact Home Assistant,
create helpers, predict their entity IDs, or alter the integration's engine.
"""

import hashlib
import json
import re


def master_number(group_id: str, entity_id: str, name: str) -> dict:
    """Describe a YAML Template Number backed by one existing relative master.

    Install through supported HA configuration after checking existing helpers.
    Resolve the resulting number entity ID from HA before binding a Bubble card.
    """
    if not isinstance(group_id, str) or not group_id.strip():
        raise ValueError("A stable group ID is required")
    if not isinstance(entity_id, str) or not re.fullmatch(r"light\.[a-z0-9_]+", entity_id):
        raise ValueError("A real light entity ID is required")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A group name is required")
    # HA evaluates names as templates too: keep arbitrary display names literal.
    literal_name = json.dumps(f"{name} Master", ensure_ascii=False)
    return {
        "unique_id": "jai_bubble_master_" + hashlib.sha256(group_id.encode()).hexdigest(),
        "name": "{{ " + literal_name + " }}",
        "icon": "mdi:tune-vertical",
        "unit_of_measurement": "%",
        "min": 1,
        "max": 100,
        "step": 1,
        "optimistic": False,
        "availability": (
            "{{ states('" + entity_id + "') in ['on', 'off'] and "
            "is_number(state_attr('" + entity_id + "', 'master_brightness_pct')) }}"
        ),
        "state": "{{ state_attr('" + entity_id + "', 'master_brightness_pct') }}",
        "set_value": [
            {
                "action": "jai_lighting.set_master",
                "target": {"entity_id": entity_id},
                "data": {"brightness_pct": "{{ value }}"},
            }
        ],
    }


def master_helper_options(group_id: str, entity_id: str, name: str) -> dict:
    """Return the equivalent native Template Number config-flow options.

    HA assigns identity for UI-created helpers. The generator must record the
    actual registry binding separately; it must not derive it from a name.
    """
    definition = master_number(group_id, entity_id, name)
    return {
        key: value
        for key, value in definition.items()
        if key not in {"unique_id", "icon", "optimistic"}
    }
