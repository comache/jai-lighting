# Bubble Card relative master adapter

The Bubble dashboard is under development on `jai-control-bubble-development`.
This step adds a configuration builder and tests; it does **not** install a
dashboard or helpers, change the lighting engine, or change existing dashboards.

## Why a native number helper is needed

In the inspected Bubble Card v3.4.1 source, a normal `light` brightness slider
calls `light.turn_on`. Changing its tap action does not replace the slider's
service call. J'ai group brightness actions retain that native ON behavior.

A Bubble slider bound to a `number` entity calls `number.set_value` instead.
`scripts/bubble_helpers.py` builds a native HA Template Number whose `set_value`
action calls `jai_lighting.set_master` on the existing group. Its displayed state
comes from that group's `master_brightness_pct` attribute. It stores no independent
master value and creates no duplicate light entity. Power must remain a separate
control targeting the original group light.

The valid range is 1–100%. An OFF group remains available for master adjustment.
If the source light is unknown/unavailable or lacks a numeric master attribute,
the number is unavailable rather than displaying a fabricated value.

## Configuration interfaces

- `master_number(group_id, entity_id, name)` returns a YAML Template Number
  definition, with an identity derived from the stable group ID.
- `master_helper_options(group_id, entity_id, name)` returns equivalent options
  accepted by HA's native Template Number helper schema.

These are pure configuration functions. They neither connect to HA nor install
anything. Resolve groups from the real catalog; do not infer an entity ID from
a group name. Before installation, inspect existing numeric helpers and their
actions. A similar name alone is not proof that an existing helper has the same
semantics. Never overwrite an existing helper to force compatibility.

After creating a helper through supported HA configuration, read its actual
entity registry ID and record the group-to-number binding. Do not predict the
generated `number.*` entity ID. UI-created helpers receive their identity from
HA; the YAML identity is not a config-flow option.

## Validation and remaining work

Tests use the pinned HA 2026.10.0 Template Number platform and real service
handlers with simulated physical lights. They cover startup without hardware
commands, master adjustment while OFF, feedback from external master changes,
relative dimming while ON, source unavailability, bounds, both configuration
schemas, and stable identity across renames.

The final Lovelace generator, actual helper bindings, popup layout, backup and
live installation remain separate steps. This adapter does not establish that a
production dashboard has been deployed or visually validated. Preserve all
existing dashboards and install only after the required fresh backup.
