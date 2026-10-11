# J'ai Lighting v0.2 implementation review

Development branch: `v0.2-development`. Manifest: `0.2.0.dev0`. Store payload:
`schema_version: 2`. Entity/topology metadata: version 1. Runtime tested against
Home Assistant Core 2026.10.0. This is a local review checkpoint, without a
v0.2 release tag, stable-branch merge, remote publication or live deployment.

## Implemented subsystems

| Module | Responsibility |
| --- | --- |
| `models.py` | Stable identities, group/leaf/capability records, bounded state and intentions |
| `topology.py` | Validated primary forest, iterative ancestry/descendant resolution, cycle/duplicate/alias rejection |
| `planner.py` | Independent relative faders, shared leaf memory, OFF behavior, RESTORE/FULL planning |
| `capabilities.py` | Legal mixed-mode aggregation, sparse property validation, compatible-only absolute planning and CT clamps |
| `presets.py` | Sparse authored rows, deepest-per-property inheritance, effective conflict validation and provenance |
| `storage.py` | HA Store snapshots, revisions, durable publication, corruption guards, before/candidate recovery journal |
| `migration.py` | Pure installation-wide preflight, owner selection, identity/memory conversion and exact original JSON record extraction |
| `manager.py` | One operation lock, HA observations, registry routing, lifecycle recovery, context-preserving bounded physical dispatch |
| `coordinator.py` | Per-group views of the same shared state; no second physical memory |
| `light.py`, `scene.py` | Hierarchical native light entities and one native scene per preset |
| `config_flow.py` | Native group/preset editors, private previews, inline recipe repair and guided legacy ownership repair |
| `services.py`, `services.yaml` | Relative master, strict absolute batch, preset activation and read-only topology actions |
| `membership.py`, `diagnostics.py` | Physical membership guards, stable routing and bounded integration diagnostics |

`__init__.py` now owns shared-manager lifecycle, migration and kind-based
light/scene forwarding. Strings and English translations cover all native steps
and actions. `engine.py` retains the original arithmetic reference and regression
coverage; production v0.2 planning uses the shared hierarchical planner.

## Identity and native editing

Existing group config-entry IDs remain light unique IDs. Reparent/name/member
changes preserve those registry associations and independent masters. Physical
registry IDs follow entity-ID renames. Registry deletion never falls back to a
previous name. Explicit entity-ID fallback remains available for unregistered
lights and has the documented rename limitation.

Group creation/editing accepts physical lights and children, explicit ownership
moves, missing-member retention, parent, order and power policy. The private
preview recomputes each affected preset's effective values and sources. Invalid
recipes must be repaired in that draft before Save. A creation token is remapped
to the actual HA-assigned entry ID, including preset targets repaired to the new
group. Stale configuration revisions reject Save.

Preset editing uses native target/property selectors and assignment switches.
Recipes remain sparse; no flattened inheritance is saved. A physical override
wins over group properties. Duplicate authored conflicts are rejected before
shadowing; final OFF/mode/effect conflicts are validated before capability skips.
Previews include provenance, compatibility skips and CT clamps. Activation plans
one intention per leaf and leaves all group masters unchanged.

Core deletion of a nonempty group retains a blocked orphan record and Repair
issue. Another group's editor can move its members/children, repair recipes and
remove the empty orphan record. Empty group and preset removals use the same
persisted journal protocol as other configuration changes.

## Storage, journal and migration

The shared `jai_lighting.state` HA Store owns topology, runtime targets and sparse
presets. Accepted state is saved before hardware dispatch; desired active output
is never assumed to be successful saved memory. ON reports reconcile the real
baseline after dispatch and startup. A failed post-dispatch save reports that
hardware may have changed; further mutations first require a fresh successful
save. No rollback or reconnect replay occurs.

Configuration preparation durably records an operation ID, before/candidate
snapshots and host intent. Creation remains unpublished until Core creates the
host. Edits/migration update hosts through public HA APIs. Removal recovery can
replay the public removal API; completion waits until the removed host is absent
from durable Core storage. This is recovery across two files, not a claim of
cross-file atomicity.

To confirm Core host writes, the adapter **reads only** Core's pinned
`core.config_entries` storage envelope (version 1, minor 5). It never calls a
migrating Store loader on that file, patches Core or writes Core storage itself.
An unfamiliar schema fails closed with the journal retained. This pinned-runtime
coupling needs revalidation for a future HA upgrade.

The v0.1 migration preflights all legacy groups before converting any. It retains
entry IDs, virtual registry associations, effective options, names, independent
masters and bounded owner-selected memories. Groups become RESTORE roots; no
root or hierarchy is inferred. Overlaps block migration until Configure selects
one primary owner, showing candidate memories. The public HA migration-retry API
loads formerly failed entries after the guided repair; ordinary reload cannot
unload `MIGRATION_ERROR`.

The backup retains original structured entry/restore records and exact JSON text
for those records present in the durable legacy files. It excludes unrelated HA
integration records and credentials. Originals are not overwritten by subsequent
migration attempts. A full HA backup remains required: restoring only source
cannot undo schema-2 hierarchy/presets or provide a lossless automatic downgrade.
Corrupt/future lighting storage remains at its original path and fails setup.
Migration, configuration saves and startup send no lighting commands.

## Validation evidence and scope

See `VALIDATION.md` for the final count and exact commands. Tests use actual
pinned HA flows, config entries, registry, Store, native light/scene services and
fresh instances, with fake physical devices. Existing arithmetic regressions are
retained. Contract changes are explicit: legacy overlapping memberships migrate
to primary ownership/hierarchy, and native configuration saves require previews.
No tests were weakened to accept failed arithmetic or unconfirmed behavior.

Coverage includes deep topology, shared faders, clipping, manual/OFF baselines,
FULL precedence, binary/brightness/CT/HS/RGB/XY/RGBW/RGBWW, effects, inheritance and
provenance, strict batches, partial failures, cancellation, timeouts, quantization,
registry deletion/reuse, draft repair, journal interruption, corruption, legacy
owner choice, save failures and restart without dispatch.

Input bounds are 10,000 topology records, 1,000 presets and 1,000 target rows per
preset or explicit control batch. Traversals do not depend on Python recursion
depth. Records/results are bounded; no unbounded event history is stored.

## Accepted limits and architecture compliance

No architecture rule was silently changed and no Core patch/interception was
introduced. The two owner-accepted boundaries remain:

- Native light calls target one group or disjoint groups; use an ancestor,
  preset or coherent domain batch for overlap.
- Native color calls accept HA preprocessing; presets/domain actions preserve
  strict original-property semantics.

Hardware gamut/mode switches and external automation races remain device limits.
RGBWW conversion skips if required bounds are not advertised. Transitions,
flash, calibration, retries, device rollback, dashboards and seasonal/non-light
orchestration are outside v0.2. Scene timestamps record attempts only. Dependency
warnings and native browser rendering are separate from real hardware/OS testing.
The native flows are tested through HA APIs; a production frontend/hardware review
has not occurred.

## Future live test plan: ONE small physical group first

This plan is a recommendation for a separately approved deployment, not an action
performed during implementation.

1. Review this branch and take a complete HA backup plus a copy of current
   integration/configuration storage. Keep the unchanged v0.1.0 source available.
2. Begin with ONE small physical group of two or three controllable lamps. Keep
   broader venue topology and dashboards unchanged during this first test.
3. Confirm startup/reload/migration send no commands. Check stable entity IDs,
   availability and missing-member diagnostics before changing output.
4. Give the lamps different levels. Verify relative increases/decreases,
   permanent clipping, manual baselines, individually OFF preservation, all-OFF
   master moves, RESTORE, FULL and explicit-brightness precedence.
5. Use one sparse preset on that same group. Check brightness/CT/color/effect
   compatibility, omitted properties, OFF property-only behavior and provenance.
6. After flat-group behavior passes, add an empty parent above that same physical
   group. Test independent parent/child masters and per-property preset inheritance;
   keep native actions to one group and use domain batches for overlap.
7. Test a controlled unavailable device, restart and rename. Confirm no replay,
   no same-name replacement binding and honest timeout/partial results. Check
   retained masters/targets after restart.
8. Review logs, observed hardware timing and backup restoration before approving
   any wider rollout. Do not expand merely because simulated tests pass.

## Local commit sequence

- `5cc8da3`: approved architecture and development scope.
- `110e19a`: domain records and validated primary topology.
- `3a5f661`: shared hierarchical relative faders.
- `b137f93`: mixed capabilities and sparse absolute properties.
- `6564fbc`: per-property preset inheritance and source tracking.
- `2842de0`: shared Store and recoverable migration preflight.
- `9106687`: fail-closed storage and exact original-record retention.
- `d49b4b0`: HA lights/scenes, native flows/actions, journal recovery and runtime tests.
- `01442f1`: explicit stale capability facts and resolved Repair issue cleanup.
- Final documentation/validation checkpoint: see the branch's final `git log`.

Preserved tag object: `f1028297e2857692e5955b53437157c3597653ce`.
Preserved v0.1.0 commit and stable `main`:
`5f6769b2ea8398e2786a9db2b4cee7593917cbec`.
