# Native Lovelace / Bubble dashboard

This is a one-time configuration generator, not another integration frontend or
live synchronizer. It adds no dependency to the lighting engine. The generated
dashboard remains a normal storage-mode dashboard, editable in Home Assistant.

## Inputs and generation

Use the supported `jai_lighting.get_topology` response for `groups`, `leaves`,
`capability_cache`, `presets`, and `collections`. Diagnostics can supply topology,
but physical routes must come from the live response or actual registry records.
Never infer entity IDs from names. Keep venue files in an ignored private directory.

Create a bindings JSON object with `groups`, `leaves`, `masters`, and `presets`
maps. Keys are stable topology IDs; values are verified HA entity IDs. Group and
physical entities are `light.*`, preset entities are `scene.*`, and master
adapters are `number.*`. Install definitions from `scripts.bubble_helpers`
through the native Template Number platform, then resolve their actual registry
IDs. See [master semantics](BUBBLE_MASTER_ADAPTER.md).

Create a presentation plan:

```json
{
  "title": "Lighting — Bubble",
  "sidebar": ["verified-group-id"],
  "views": [
    {"id": "interior", "name": "Interior", "groups": ["verified-group-id"], "color": "#edbfd1"}
  ],
  "quick_switches": []
}
```

Optional `spotify` is a discovered `media_player` entity. Optional
`quick_switches` contains `{ "entity_id": "switch.actual", "name": "Display name" }`
entries. View names/IDs should come from actual Floors/Areas where appropriate;
group placement is an explicit presentation choice, independent of hierarchy.
Never use the illustrative IDs above in production.

```sh
.venv/bin/python -m scripts.bubble_dashboard \
  --snapshot PRIVATE/topology.json \
  --bindings PRIVATE/bindings.json \
  --plan PRIVATE/plan.json \
  --output PRIVATE/dashboard-new.json
```

Output creation is exclusive. Regeneration fails if the output already exists;
use a new filename and a new HA dashboard to compare before transferring edits.
The generator never connects to HA or overwrites an installed dashboard.
JSON is valid YAML and can be pasted into the new dashboard's raw editor.

## Layout and controls

- Native Sections views, one information column and a two-column control area.
  The sidebar is generated from one definition in each view, not hand-maintained
  copies. HA handles responsive stacking. It remains present across navigation;
  it is not a custom fixed-position, independently scrolling sidebar.
- Bubble Card **3.4.1** sliders target Template Number adapters; power sub-buttons
  target the existing group lights. No duplicate light entities are created.
- Group details are standalone Bubble popups generated from immediate children
  and direct leaves. Hashes derive from stable IDs, never hierarchy paths.
  Previous-popup navigation supports nested groups. No permanent live sync exists.
- Up to two child power shortcuts accompany the power/details controls.
- Real presets call `jai_lighting.activate_preset`. Collections use one domain
  `jai_lighting.control` batch, so overlapping membership is resolved by the engine.
  Empty catalogs display text, never invented functional controls.
- Spotify uses the existing Mushroom media card. No playback is started on load.
  An idle player may require the owner to choose a Spotify Connect device.

Dependencies: existing Bubble Card and, if Spotify is configured, Mushroom.
No card-mod, layout-card, new framework, or dashboard designer is installed.

## Installation and verification

Before any configuration write, create a fresh full HA backup and verify the
archive plus its Home Assistant payload. Record the installed integration commit,
existing dashboards and topology, and the new dashboard's source generator commit.
Preserve existing config and all registry identities. Do not write `.storage`
directly. Create the new storage-mode dashboard through HA's dashboard UI.

Template Number definitions may be installed through supported YAML includes or
the Template helper UI. Adding the Template platform for the first time through
YAML can require a Core restart. Check configuration before restarting. Startup
and helper creation do not invoke `set_value`; this is covered with simulated
hardware tests against pinned Core 2026.10.0.

Verify actual HA rendering, popup navigation, real bindings, existing dashboards,
and logs read-only. Do not test real power, sliders, presets, or media actions.
Offline engine tests use only simulated lights. Leave physical acceptance to the
owner, beginning with one small group while it is OFF.

## Kiosk and limitations

No kiosk plugin is assumed. Native sidebar collapse and device fullscreen can be
used without globally changing authentication or other dashboards. Full
dashboard-specific navigation hiding requires a separately reviewed kiosk
integration; it is not silently installed or configured here.

Native Sections controls responsive placement, not a guaranteed sticky desktop
sidebar. The generated sidebar is present in every view and stacks on mobile.
Topology edits require a deliberate new export/regeneration or visual edits.
An unavailable source group makes its numeric adapter unavailable by design.

## Rollback

Keep the full matching backup and a pre-install copy of `configuration.yaml`.
The new dashboard can be removed through the UI without affecting existing ones.
If rolling back helpers, remove only the newly added Template definitions/include
after ensuring no owner edits depend on them. For a failed deployment involving
new configuration data, restore the matching complete HA backup and prior source;
do not perform an unsafe integration-only downgrade.
