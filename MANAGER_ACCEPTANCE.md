# Manager compatibility and acceptance

Implementation branch: `topology-manager-development`, based on main `aea79db`.
Integration version remains `0.2.0.dev0`; Home Assistant Core pin is 2026.10.0.
No release, tag, main merge, live deployment or live configuration edit was made.

## Automated validation

Final validation on 2026-10-11:

| Check | Result |
| --- | --- |
| `.venv/bin/python -X faulthandler -m pytest -q` | 147 passed in 84.10 s; 6 HA/aiohttp/backoff warnings |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed; 42 files formatted |
| `node --test tests/frontend/*.test.mjs` with Playwright/Chrome | 11 passed; none skipped |
| Prettier 3.6.2 format check | Passed |
| `git diff --check` | Passed |

Node tests use pinned Playwright 1.62.1; the validation environment used its
bundled runtime and locally installed Chrome. A normal checkout can use `npm ci`,
`npx playwright install chromium`, then `npm run test:frontend`. No npm dependency
is loaded by the shipped manager at runtime.

Python tests use
the installed pinned HA implementation, isolated configuration directories and
fake light entities. They cover real websocket validation/admin authorization,
native group/preset creation, public panel/static registration, registry identity,
Store, restart and interrupted journal recovery. Frontend tests run the actual
custom component against a generic loopback fixture transport, separately from
the real Python backend tests. These are not a full HA OS browser end-to-end test.

Browser coverage includes review/save, empty first root, false/zero/no-parent
values, combined label/area/device filters, explicit collection additions,
graphical sparse preset rows, desktop drag, real touch pointer events and Move to
alternatives. Layouts are checked at 390, 820 and 1440 pixels. Actual Safari,
iPhone, iPad and hardware acceptance remains pending.

## Compatibility and migration

- Existing config entries, group/preset IDs, entity registry unique IDs and
  surviving entity IDs are retained. Renames update names through public APIs.
  Existing Bubble Card/automation references continue to use the same light
  entities; actual Bubble Card rendering was not part of the isolated harness.
- Storage remains schema 2 in the same HA Store. Old schema-2 data lacking the
  new optional `collections` map loads with an empty map. Existing groups,
  recipes, masters, targets and capability caches remain. No venue data is added.
- Collections reference stable leaf records and never own primary parents.
  Removing primary membership keeps the leaf as Unassigned, including references
  and bounded state. A collection can add a light that has no primary group.
  Registry-backed missing members remain missing; adding another light does not
  discard them or bind them to a same-name replacement.
- Existing v0.1 preflight/journal behavior is retained and tested with additive
  collections. Migration and startup do not send lighting commands. Unknown or
  corrupt storage fails closed. Full backups remain necessary before migration.
- Downgrade is not automatically lossless. The preceding code cannot represent
  unassigned leaves or collection recipe targets. Restore the complete previous
  HA backup with the matching source; do not just replace code after using these
  features. The existing immutable v0.1.0 tag remains preserved.
- Validation runs on copied whole drafts before publication. Configuration
  revisions reject stale editors. Existing journal recovery reconciles Store and
  config-entry changes after interruption; no private registry mutation is used.
  Failed Store writes publish no candidate state. Configuration saves preserve
  newer runtime observations/controls made while a preview was open.
- Directly removing a nonempty HA config entry still retains a blocked orphan
  and Repair issue. Prefer the manager's confirmed deletion: select a destination
  for direct children/lights, preserving descendant subtrees. A preset referencing
  the removed group/collection must first be edited; no automatic recipe rewrite
  is performed. Rename and config-entry deletion are separate transactions.

## Control and preset contracts

Collections have no native virtual light entities or independent faders.
Manager ON/OFF and absolute brightness reuse existing capability filtering,
shared state, persistence and physical dispatch. Absolute brightness alone
preserves OFF state, as existing property-only controls do. Use ON followed by
brightness, or a preset assigning both, when power-on is intended.

One batch deduplicates overlapping collection intentions per physical leaf.
Different values for the same physical property are rejected. Separate controls
serialize under the existing domain lock; the later accepted request uses the
state after the earlier request. External automations/devices remain outside
this lock, as before.

Preset precedence is per property: deepest hierarchy group, then collection,
then explicit physical leaf. Differing properties from overlapping collections
are rejected even if a physical override would shadow them; equal properties
deduplicate with stable source tracking. Final inherited-state validation happens
before capability filtering. Presets never modify memberships or group masters.

Labels are explicit selection helpers only. The browser combines all selected
filters; labels include entity, device and effective area labels. The backend
also accepts multiple labels requiring all of them. Stored membership contains
leaf identities, never label expressions or dynamic rules. Select all matching
adds matching lights to the current explicit selection; uncheck exceptions.

## Separate engine findings

The real HA native `light.turn_on` brightness service powers an OFF group ON.
HA performs its normal percentage preprocessing before calling the entity.
This is the existing accepted native-service boundary; it is not changed here.
The manager relative slider calls `jai_lighting.set_master`, which adjusts retained
OFF targets without a physical call. The regression checks both paths.

After an ancestor native command, child entity state updates from shared physical
observations while its independent master stays unchanged. Regression tests
verify this propagation and preserve the earlier relative scaling/manual-baseline
tests. No Core interception, planner rewrite or lighting-semantic workaround was
introduced.

## Manual acceptance, before approving deployment

Use a backed-up isolated HA 2026.10.0 test installation first. Observe service
calls/logs and physical state throughout editing; configuration changes must
send zero light commands.

1. As an administrator, verify the J'ai Lighting sidebar and all four sections.
   Confirm a non-admin cannot open the panel or mutate through its websocket API.
2. Create an empty first root, then one child. Rename each and note unchanged
   entity IDs. Add one small physical group only after the empty flow passes.
3. Expand/collapse, search, select multiple lights, move between subgroups/root,
   reorder lights and sibling groups. Try a cycle and ancestor+child selection;
   the draft must be rejected without changing stored topology.
4. On desktop, iPad and iPhone, move a light/group by drag; use Move to/up/down
   as alternatives. Check portrait/landscape, scrolling, keyboard submission,
   dialog focus, long names, duplicate names and dark/light HA themes.
5. Combine entity/name, area, label and device filters. Select all, uncheck one,
   add an unlabelled light and save. Change labels afterward and confirm unchanged
   group/collection membership. Keep a missing registry member through editing.
6. Create two overlapping cross-branch collections. Confirm primary parents stay
   unchanged. Explicit ON/OFF/brightness controls should issue at most one command
   per shared light in a single batch. Check binary and unavailable-light skips.
7. Create a hierarchy/collection/physical-override recipe. Inspect final effective
   values/sources; conflicting overlapping collections must reject Save. Activate
   only after reviewing the recipe. Verify existing scenes and Bubble Card controls.
8. Open a preview in two sessions. Save the first, then attempt the second; stale
   edits must fail. Change a retained master while a valid preview is open; Save
   must preserve that newer runtime state.
9. Confirm deletion refuses unchecked confirmation and referenced presets.
   Repair a preset, delete its former group with a chosen destination, and verify
   surviving subtree/leaf/collection IDs. Test ordinary native entry removal's
   orphan Repair path separately in the isolated installation.
10. Restart the test HA installation. Verify topology, memberships, recipes,
    masters/targets and entity IDs persist and startup sends no hardware commands.
    Simulated failures/journal interruption are automated tests; avoid manually
    corrupting the live Store to reproduce them.

## Installation instructions for later approval only

These steps have not been executed against any live installation.

1. Review and approve the development branch and manual results. Take a full HA
   backup, including configuration entries, registries and `.storage`, and record
   the installed source commit. Stop if HA/Core versions differ from the tested pin.
2. Install the reviewed `custom_components/jai_lighting` directory including its
   `frontend` files through the usual custom-integration method. The shipped JS
   has no npm build step or runtime npm dependency. Do not copy `.venv`, tests,
   development dependencies, caches or local test configuration to HA.
3. Restart HA, examine setup/Repair logs and open the admin sidebar. If old assets
   persist, refresh the browser/app cache. Verify startup and read-only inspection
   send no commands before creating/editing anything.
4. Complete the empty-root flow, then repeat the acceptance plan on ONE small
   physical group. Only explicit controls/scene activation may send commands.
   Expand to more lights after reviewing results; do not import a venue topology.
5. If rollback is required after new manager features were saved, restore the
   matching full pre-installation backup and source. Preserve the failed evidence
   for review. No release/tag or automatic deployment is part of this task.
