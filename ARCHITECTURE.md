# J'ai Lighting architecture

Design specified before implementation, 11 October 2026. Target: Home Assistant
Core 2026.10.x on Home Assistant OS 18.3. OS deployment requires no OS changes.

## API verification

Inspected the official **2026.10.0 tag**, rather than assuming older examples apply:

- [LightEntity and color modes](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/light/__init__.py): subclass `LightEntity`, advertise `ColorMode.BRIGHTNESS`, expose async power methods.
- [Light services](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/light/services.py): `light.turn_on` with brightness means power on; brightness zero is routed to turn off. Thus a separate `jai_lighting.set_master` entity action is necessary to adjust brightness without powering on.
- [Config entries](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/config_entries.py): `ConfigEntry.runtime_data`, async platform forwarding/unloading, update listener; `OptionsFlow.config_entry` is supplied by the framework (do not assign it in the constructor).
- [Group flow](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/group/config_flow.py) and [selectors](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/helpers/selector.py): use `probatio` schemas and a multiple light entity selector; not the former voluptuous API.
- [RestoreEntity](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/helpers/restore_state.py): `ExtraStoredData`, `async_get_last_extra_data`, and `extra_restore_state_data` retain internal data even when off/unavailable hides standard brightness.
- [Entity platform](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/helpers/entity_platform.py): platform entity service registration provides standard entity/device/area targeting.
- [Core packaging](https://github.com/home-assistant/core/blob/2026.10.0/pyproject.toml): Python >=3.14.2, probatio 0.13.0.
- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/): one custom component domain, versioned manifest and repository metadata.

## Components and identity

One config entry represents one group. Repeating Add Integration creates additional
groups. Name and membership are edited through Options Flow. The entry ID supplies
the virtual light's stable unique ID; the initial suggested entity ID is
`light.jai_<name>`. Membership edits retain that entity and its master state.

`engine.py` holds a pure state model and command planner. `coordinator.py` adapts
Home Assistant states and dispatches commands. This coordinator is event driven,
not a polling DataUpdateCoordinator. `light.py` implements LightEntity and
RestoreEntity. `config_flow.py` validates UI configuration. No helpers, core
patches, physical integration changes, or external dependencies are needed.

## State invariants

- Master is an independent fader, never a mean/max of members. Initial value is
  100%; editing physical lights never moves it.
- Store master in HA units: integers **3..255** (3 is the first representable byte
  at or above 1%). `set_master` takes 1..100 percent; ratios use the stored bytes.
  Output rounds half up to whole bytes. Numerical examples in percentages are
  approximate at byte resolution.
- Member memory is only a bounded **1..255** last observed brightness, or a
  bounded off-state restore target. No unclipped values or hidden trim factors.
- A member observation includes availability, power, dimming capability and
  reported brightness. An on member with missing brightness is skipped for
  master changes rather than inventing a baseline. On/off-only lights receive
  power actions only. RGB/temperature lights receive brightness without color.
- Group `is_on` is true iff any available member is on. Availability is true iff
  at least one member reports on/off; missing/unknown/unavailable members are
  skipped. Expose unavailable members and last command failures as attributes.

## Operations

**Active master movement:** sample each member's real current HA state under a
shared integration command lock. For each available, on, dimmable member with
reported brightness, send `clamp(round(actual * new_master / old_master), 1, 255)`.
Individually off members stay off. No cached target substitutes for an on light's
reported brightness. Repeating the same master value is a no-op.

**Turn off:** capture actual on brightness before dispatching off to every
available member. Preserve master. Repeated off does not overwrite saved targets
with stale brightness attributes from off lights.

**Master movement while all members are off:** scale the bounded saved targets
once for every movement, clipping and rounding at each step. Dispatch no power
or brightness calls. This intentionally has the same clipping loss as active
movement, e.g. 70 -> 100 -> 35 is not equivalent to 70 -> 35 after clipping.
If no on observation exists, bootstrap an off light's brightness attribute, or
use 255 on its first turn on. An unavailable member with saved data can have its
off-state target adjusted but receives no commands.

**Turn on:** turn on all available members, including individually off members,
using saved brightness (or the off brightness attribute, otherwise 255).
Already-on members retain their current baseline. An optional brightness argument
first moves the master relative to its previous value, then powers on the off
members. `light.turn_on(brightness=0)` follows HA semantics and powers off;
it never resets master. `jai_lighting.set_master` preserves current power state.

**Manual control:** member state events update memory only from real on values.
Manual on makes the group on immediately. Manual off retains last observed level.
Physical on observations always supersede saved off targets. Adjusting an off
physical light's retained brightness attribute without turning it on is not an
authoritative observation; use group set_master for off-state fader movement.

## Concurrency, acknowledgement and failures

All J'ai groups share one async command lock so overlapping direct memberships
serialize. Deduplicate member IDs before planning. Dispatch independent physical
commands concurrently. Use blocking HA service calls with inherited context and
wait up to two seconds for changed member state where the target differs. After
acknowledgement, the next operation reads actual state (including device
quantization). A timeout exposes a failure; no guessed device result or automatic
retry. Late device reports still update the group. Transitions are not advertised
in v1, avoiding deliberate intermediate fader baselines.

The new master is accepted once a command is planned, including partial failure.
Attempt remaining members, record failed/timed-out IDs, and raise a descriptive
HomeAssistantError after updating entity state. Repeating the same master does
not rescale successful members. Manually controlled devices and independent HA
automations can race a group action; no integration can transact arbitrary devices.

## Persistence and configuration changes

Restore schema version 1 contains master and bounded member memory. Restore via
ExtraStoredData on startup/reload, prune removed IDs, then reconcile current on
observations. Never send commands on startup. Real on values win over saved data;
off values preserve pending scaled targets. Invalid/version-mismatched data falls
back safely to defaults. HA's restore mechanism persists periodically and on
graceful shutdown; abrupt power loss can lose changes since the last save.

Options reload changes only membership/name. New members start from their own
state; replacements do not inherit the removed light's brightness. Unknown IDs
cannot be selected as new members; already configured missing lights remain
editable/removable. Entity IDs are membership keys in v1: after a physical entity
ID rename, update membership in Options. Registry-stable member references can
be added through a future storage migration.

## Topology and explicit v1 limits

Reject J'ai entities and other aggregate lights (state `entity_id` membership or
registered `group` light platform) as members, in both UI validation and runtime.
This prevents recursion and hidden duplicate physical paths. Other virtual lights
cannot always be identified from HA's public state; only select physical leaves.
Direct overlap between separate groups is supported; each operation affects a
physical leaf once, and the other group's master does not move.

The engine accepts a deduplicated leaf observation map. A future topology resolver
can expand a group DAG into this map, reject cycles, and assign a single factor per
leaf before dispatch. It must never implement nesting by forwarding master calls
to child virtual lights. Nested master synchronization policy remains a future
design decision; nesting is disabled in v1.

## Verification

Test pure ratio/clipping/manual-baseline behavior and all requested scenarios.
Also test real HA setup/unload, flows, entity service targeting, mixed capabilities,
event updates, delayed acknowledgements, failures, and restoration. Pin the HA
runtime to 2026.10.0; document runtime or device validation limitations accurately.
