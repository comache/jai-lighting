# J'ai Light Manager

Home Assistant groups with independent relative master faders, a shared physical
lighting hierarchy and absolute preset scenes. Names, topology, lights, presets
and policies are configured by the user. The integration contains no venue
configuration or dashboard layouts.

This checkout is **0.2.0.dev0**, including the topology manager and v0.2 engine.
The stable **v0.1.0** baseline remains at its original annotated Git tag. The
repository is [comache/jai-lighting](https://github.com/comache/jai-lighting).
The owner confirmed public repository visibility on 2026-10-11 and authorized
the manager merge and production deployment after a verified full HA backup.
No new release or version tag is created. Repository validation and verification
of the actual running installation are separate requirements.

Development and validation target **Home Assistant Core 2026.10.0** and Python
3.14. Tests use isolated HA directories and fake physical devices. Hardware and
HA OS validation remain separate review steps. The approved specification is
[V0.2_ARCHITECTURE.md](V0.2_ARCHITECTURE.md); implementation and recovery details
are in [IMPLEMENTATION_V0.2.md](IMPLEMENTATION_V0.2.md).

## Manager

Administrators can open **J'ai Light Manager** in the HA sidebar. Hierarchy provides
group creation, rename/policy, subtree moves, explicit membership, ordering,
multi-selection, search, desktop/touch drag-and-drop and Move to / up / down
alternatives. Every edit is reviewed and revision-checked before Save.

The physical light browser combines name/entity ID, area, label and device
filters. Select all matching adds an explicit selection; changing labels later
never changes stored membership. Labels include entity, device and effective
area labels. Remove primary membership retains a light as Unassigned, preserving
its collection/preset identity. Confirmed group deletion promotes direct children
and lights to the chosen destination; referenced presets must be repaired first.

Collections cross primary branches and may overlap. They have explicit members,
absolute compatible brightness and ON/OFF controls, without primary parents,
relative masters or new HA light entities. One batch deduplicates shared lights;
separate controls serialize in acceptance order. Presets accept collection targets
between hierarchy assignments and physical overrides; conflicting overlapping
collection properties are rejected. Existing scenes and group entities remain.

Settings shows storage/recovery status and links the existing native editors.
See [TOPOLOGY_MANAGER.md](TOPOLOGY_MANAGER.md) for the audit and plan, and
[MANAGER_ACCEPTANCE.md](MANAGER_ACCEPTANCE.md) for compatibility, validation,
manual acceptance and later deployment instructions.

## Configuration

In an isolated review installation, **Settings → Devices & services → Add
integration → J'ai Light Manager** offers **Add lighting group** and **Add preset**.
No YAML is required to create or edit either.

Each group has a name, optional parent, direct physical lights, child groups,
sibling order and bare-ON policy. Multiple roots and empty construction groups
are allowed. A physical light belongs to one primary group; a group has at most
one parent. Confirm an ownership move explicitly. Cycles, duplicate identities
and aggregate lights selected as physical leaves are rejected.

**Configure** edits the existing entry. Review the resulting topology and
changed preset values/sources before Save. Invalid presets can be repaired inside
that same workflow. Renaming/reparenting a group retains its virtual entity's
registry identity and master. Replacing a light gives the new device its own
baseline, without copying the removed device's memory. Direct physical overrides
in presets require explicit repair when that leaf is removed.

Registry-backed lights follow entity-ID renames. If their registry entry is
deleted, membership becomes missing and never binds to a same-name replacement.
Unregistered lights use their exact `light.*` ID, with an explicit rename
limitation. Keep missing memberships through the editor until deliberately
repaired or removed.

Deleting a nonempty group's config entry retains its topology as a blocked
branch and creates a Repair issue. Use another group's Configure flow to move
its lights/children and repair affected recipes. This prevents deletion from
silently dropping ownership or broadening preset scope.

## Independent faders and power

A master is an independent position, initially 100%; it is not average lamp
brightness. Moving a parent from 50% to 75% multiplies eligible real lamp levels
by 1.5 and leaves child masters unchanged. A subsequent child move uses that
child's previous position and the lamps' current reported levels.

Clipping permanently loses excess. Integer brightness uses half-up rounding:
masters 3..255 and physical levels 1..255. A same-position request is a no-op,
even after an absolute preset. Manual ON brightness becomes the next baseline.
Master moves preserve color, effect and power.

When any descendant is ON, relative moves affect available ON dimmable lights;
individually OFF lights and their saved levels stay unchanged. When all available
lights are OFF, the addressed master's move scales shared saved targets without
commands. Existing targets of unavailable lights can also scale in this case.
No operation is queued for reconnect.

| Request | Behavior |
| --- | --- |
| Bare ON, RESTORE | OFF lights restore saved levels; already-ON lights retain theirs |
| Bare ON, FULL | Every available dimmable light receives full brightness |
| OFF | Save observed ON levels and turn available descendants OFF |
| Native ON with brightness | Relative master change plus RESTORE, overriding FULL |
| Native ON with color/CT/effect | RESTORE plus compatible explicit fields |
| `set_master` | Relative movement with power preserved, including while OFF |

Use the native **J'ai Light Manager: Set relative master** action for an OFF fader
move. Normal HA brightness controls turn lights ON. Presets and absolute control
leave every group master unchanged and bypass bare-ON policies.

## Preset scenes

The editor provides name, add/edit/remove target assignments, group/collection/physical
selectors, assignment switches, brightness/temperature/color/effect selectors,
and a final per-leaf effective-state preview. Every preset creates one native
`scene.*` entity. Its timestamp records an activation attempt, not success or a
persistent active-scene state.

Recipes are sparse and absolute. The most specific target wins **per property**:
a child's brightness can override a parent while inheriting its ON and CT.
Collections override hierarchy properties; physical-leaf assignments are most
specific. Conflicting overlapping collection properties and assignments at the
same specificity are rejected, even when a deeper row would shadow them.

Omission means inherit, otherwise leave unchanged. A property-only recipe does
not turn an OFF lamp ON and does not defer a change for its next ON. Final
inherited OFF+properties, color+CT and effect+color/CT conflicts are rejected
before capability filtering. Unsupported deeper assignments do not fall back
to ancestor values.

Mixed groups expose only legal HA color modes. CT applies to native CT lights
with per-leaf clamping. Tagged HS/RGB/XY colors convert deterministically to a
supported physical format. RGB chromaticity is normalized; color-only commands
omit brightness. RGBWW conversion requires advertised temperature bounds;
missing bounds cause a reported skip. Effect names must match advertised names.
Hardware mode switches may alter perceived output or stop an effect.

## Domain actions and accepted HA boundaries

| Action | Contract |
| --- | --- |
| `jai_lighting.set_master` | Native HA targets plus `brightness_pct` in 1..100; one group or disjoint groups |
| `jai_lighting.control` | Native targets plus sparse `properties`, or a list of `{entity_id, properties}` assignments |
| `jai_lighting.activate_preset` | Exactly one J'ai preset scene; optional detailed response |
| `jai_lighting.get_topology` | Read-only versioned topology and retained state; response required |

Sparse absolute properties are `power` (`on`/`off`), `brightness_pct` (>0..100),
`color_temp_kelvin`, `color: {kind: rgb/hs/xy, value: [...]}` and `effect`.
Control batches combine identical/disjoint assignments; conflicting assignments
to the same leaf/property are rejected without preset inheritance. A coherent
operation creates at most one physical intention per leaf.

Native `light.*` actions should target one J'ai group or multiple disjoint groups.
For parent+child/overlap use the common ancestor, a preset or domain control batch.
Native color requests accept HA's preprocessing; strict original-property
semantics are provided by presets/domain control. Core is never patched or
intercepted. Transitions, flash and automatic retries are not advertised.

## Persistence, migration and failure

One versioned HA Store owns topology, sparse presets, independent masters and
shared bounded targets. Accepted state is saved before dispatch. Current ON
observations reconcile after commands and on startup, with no startup commands.
An interruption journal reconciles Store/config-entry changes before control.
Unknown storage versions or corrupt data fail closed and preserve the file.

Take a full HA backup **before** a v0.1 migration. Installation-wide preflight
converts flat groups to independent RESTORE roots, preserving entry/registry
identities, names, masters and bounded memory. Legacy overlaps require choosing
one primary owner in Configure; the chosen owner's saved target wins. Original
entry/restore records and available exact JSON record text are retained. Migration
sends no hardware commands. Downgrade requires the pre-migration HA backup plus
the unchanged v0.1.0 source tag; there is no automatic lossless flattening.

One domain lock serializes operations. Up to eight physical calls run in parallel,
with a ten-second service timeout and two-second report timeout. Partial failures
attempt other leaves, retain accepted masters and report uncertainty. Device
brightness quantization is distinguished from exact acknowledgement. No rollback,
replay or external-automation transaction is claimed. A post-dispatch save failure
reports that devices may have changed and requires a fresh successful save.

Metadata and diagnostics expose hierarchy, IDs, coverage, mixed/unknown values,
missing leaves and bounded results. They contain no dashboard layout. Abrupt
power loss can lose recent external observations before their coalesced save.

## Development checks

```sh
.venv/bin/python -X faulthandler -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npm ci
npx playwright install chromium
npm run test:frontend
```

Dependencies are pinned in `requirements-dev.txt`. The original v0.1 arithmetic
regressions remain; overlapping membership and preview-confirmation tests now
exercise the approved changed contracts. See [VALIDATION.md](VALIDATION.md).
The Manager UX review, context-preservation behavior and deployment checklist
are documented in [MANAGER_UX.md](MANAGER_UX.md). Run `npm run preview:manager`
for an isolated synthetic browser preview.

The manager browser harness serves only generic fixtures on loopback. It does
not connect to an HA installation; pinned Python tests exercise real HA APIs.
