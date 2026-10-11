# Topology manager audit and implementation plan

## Audit (before implementation)

Baseline: main `aea79db`, Home Assistant 2026.10.0, 121 passing tests,
manifest `0.2.0.dev0`. Released tag `v0.1.0` remains immutable.

- `models`, `topology`, `planner`, `capabilities`: validated single-parent forest,
  registry identities, shared physical targets, independent relative masters,
  compatible sparse absolute planning. Keep these lighting contracts.
- `storage`, `migration`, `manager`: schema-2 HA Store with before/candidate
  journal and revision checking; public config-entry operations; legacy backups.
  Existing groups are config-entry IDs and light unique IDs; presets are scene
  unique IDs. Renames update display names without renaming entity IDs.
- `config_flow`: native create/edit, move confirmation, preview, preset repair,
  guided migration. The first-root frontend schema bug is fixed. No graphical
  forest editor, bulk mutations, label browser or control collections exists.
- `presets`: sparse most-specific-per-property recipes and provenance; only
  hierarchy groups and physical leaves currently supported.
- `light`, `scene`, `services`: preserve public light/scene identities and domain
  services. Existing Bubble Card references continue to target the same entities.
- Tests cover pure engine/model and real pinned HA flows, registries, Store,
  dispatch, failures, migrations and restart. No frontend browser harness exists.
- README/implementation/validation documents describe older review checkpoints;
  these are historical evidence, not an accurate statement of the current Git
  merge or the owner's subsequent live test. No manager deployment is authorized.

## Incremental plan

1. Add explicit collections and unassigned physical records as additive schema-2
   fields. Preserve old records, schema/version numbers, journals and identifiers.
   Existing files load with an empty collection map; unknown schemas fail closed.
2. Add revision-checked topology mutation and inventory APIs. Route create through
   existing native config flows, edits/removals through the existing journal.
   Deletion requires confirmation, promotes children/members to a chosen parent
   (or unassigned/root), and blocks dangling preset/collection references.
3. Register an admin-only custom sidebar panel through HA's supported custom
   panel/static-resource APIs, with authenticated admin websocket endpoints.
4. Implement responsive tree, selection, search/filtering, drag/drop and equivalent
   Move/reorder actions. Label/area/device filtering resolves once to explicit
   membership; later registry changes never change that membership.
5. Add collection controls and collection recipe targets using the existing
   sparse absolute planner, operation lock and physical dispatch.
6. Test backend, persistence/recovery, existing entity/control compatibility and
   frontend interactions in isolation; document manual device/browser acceptance.

## Explicit extension decisions

- A known physical leaf may have no primary parent. It remains one stable record
  for collection/preset use; hierarchical resolution never includes unassigned
  leaves. Existing assigned records and independent masters are unchanged.
- Collections store explicit ordered leaf IDs, may overlap, and do not create
  primary parents or relative masters. Collection brightness is absolute.
- Preset precedence: deepest hierarchy group, then collection, then physical leaf,
  per property. Different values from overlapping collections are rejected even
  if a later leaf override would shadow them; equal assignments deduplicate.
- Separate control calls serialize through the existing domain lock; later
  accepted calls apply to shared leaves after earlier calls. A single collection
  batch deduplicates equal intentions and rejects conflicting leaf properties.
- Native HA brightness services keep their existing ON behavior. The manager
  relative fader uses `set_master` semantics; deferred engine findings are separate.

Implementation status and acceptance instructions will be updated after checks.
