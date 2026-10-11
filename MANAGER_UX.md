# Manager UX review

Implemented on `manager-ux-development`, based on main `f29a09a`.
This branch is for owner review. It has not been merged or deployed.

## Interaction and layout

The existing admin-only Home Assistant panel now has a compact hierarchy,
contextual toolbar, inspector and physical-light browser. HA theme variables
provide light/dark colors. Header, tabs and management controls remain outside
list scrolling. Desktop and sufficiently wide tablets show two columns. Narrow
tablets and phones use Hierarchy / Available Lights buttons; neither panel is
pushed below a long document. Lists fill the remaining viewport with independent
scrolling. The compact inspector exposes group metadata and deliberately
collapsed lighting controls separately from organization.

Groups show actual direct child/light counts, descendant totals, unavailable
counts and aggregate ON/OFF/mixed state. Collapse buttons do not select rows.
Mouse/keyboard selection supports single, Cmd/Ctrl, Shift ranges, arrows,
Home/End, Space, Enter and Escape. Right-click exposes contextual actions;
disabled buttons explain their required selection. Tree rows have no permanent
checkboxes. Explicit light-browser and collection membership selections retain
checkboxes.

One form-associated hierarchical picker is shared by create/parent, Move,
subtree relocation on deletion and preset group targets. It preserves sibling
order, searches names with ancestor context, highlights the destination, disables
self/descendant destinations, supports keyboard/touch, and bounds its popup to
the viewport. Root / Unassigned is distinct. Group IDs are never printed in
normal picker labels. Collection and physical preset targets remain selectable
in this same component.

Hierarchy inventory contains only unassigned physical lights, even with label,
area, device or name filters. Collections can select all physical lights and
then make explicit individual membership exceptions. Labels are selection aids,
not dynamic membership rules. Adding lights shows both destination and actual
eligible selection count.

Drag supports whole subtrees, multiple selected items, before/after siblings,
inside groups and installation root. Selected descendants of selected groups are
not moved twice. Invalid cycles are rejected before save and again by the server.
Edge scrolling affects only the panel under the pointer. On narrow screens,
holding a drag over the panel switcher opens the other panel. Touch handles and
Move / Add / Move up/down alternatives cover devices without native desktop
HTML drag support. Dragging an unselected browser light retains unrelated browser
selections; modifier dragging a selected light moves that light alone.

## Saving and context

Ordinary moves, sibling reorders, add/remove memberships and group name/policy
edits auto-save through serialized preview/validation and revision-checked
transactions. Safe optimistic updates reconcile with authoritative data. Failures
restore the confirmed state; stale revisions retain the other administrator's
changes and explain the conflict. Creation, destructive deletion and existing
collection/preset recipe review retain explicit confirmation.

A small keyed DOM reconciler retains scroll containers and unchanged rows.
Each list retains its first surviving visible anchor, with a clamped numeric
fallback. Tree, browser, collection and inspector scrolling are independent.
Selections use stable IDs and are reconciled rather than cleared wholesale.
Partial assignments remove only assigned selections. Tab/panel round trips
preserve valid selections, filters, search and scrolling. Expansion is separate
from stored topology and persists in browser storage per HA user, including
reload; storage-denied private browsing uses in-memory expansion.

HA's existing manager WebSocket event triggers a debounced authoritative update.
Events during saves/dialogs are deferred safely. HA-provided physical states
update availability immediately, including unassigned lights. There is no
polling loop or page-navigation refresh. Refresh remains an explicit sync action.

## Compatibility and Undo boundaries

No engine/planner, storage schema, migration, identities, relative faders,
retained brightness, preset precedence, physical dispatch or registry metadata
was redesigned. Existing group/entity IDs and Bubble Card/dashboard references
remain valid. Configuration edits do not dispatch physical lighting commands.
Unavailable devices stay unavailable; organization does not repair them.

The management transport adds inverse operations to successful ordinary-edit
responses. `restore_positions` is a normal validated candidate operation, guarded
by the existing revision, lock, journal and persistence/recovery path. Undo never
restores an obsolete runtime snapshot. New physical identities are retained as
unassigned records when an assignment is undone. HA device registry iteration
uses the supported iterable rather than the deprecated `.values()` accessor.

Undo covers the last successful ordinary edit in the current panel session.
Another configuration revision invalidates it. It does not undo creation,
destructive deletion, collection/preset recipe edits or lighting actions. This
is intentionally bounded; there is no persistent multi-step Undo journal.

## Automated validation

Pinned Home Assistant Core: **2026.10.0**.

- Python: **157 passed**, six upstream dependency/runtime warnings.
- Ruff check: passed. Ruff format check: **44 files formatted**.
- Frontend: **42 passed**, including mouse and Chromium touch-event tests.
- Prettier: passed for frontend modules, tests and preview script.
- Synthetic performance fixture: **200 groups, 300 physical lights, 20 levels**.
  Search measured about 12 ms and keyed render about 26 ms on the development
  Mac; regression thresholds are 500 ms. These are local measurements, not a
  guarantee for every device.

Coverage includes real HA transport/persistence Undo and conflicts, no-dispatch
assertions, expansion/reload, picker ordering/search/invalid destinations,
viewport bounds, phone/tablet/desktop layout, independent scrolling, stable DOM
identity, surviving anchors, partial selection, rollback, concurrent edits,
serialized saves, external events, theme tokens and existing OFF-safe controls.
Browser tests run only against synthetic loopback fixtures; they never connect
to the live HA OS installation. Actual Safari/iOS and HA OS frontend acceptance
remain to be done after owner approval. Modern browser support for form-associated
custom elements and popovers is required.

```sh
.venv/bin/python -X faulthandler -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
JAI_BROWSER_CHANNEL=chrome npm run test:frontend
node_modules/.bin/prettier --check custom_components/jai_lighting/frontend/*.js tests/frontend/*.mjs scripts/*.mjs package.json
npm run preview:manager
```

Without `JAI_BROWSER_CHANNEL`, Playwright uses its installed Chromium. The preview
prints a loopback-only URL and uses synthetic data. Add `?large=1` for the scale
fixture, or `?theme=dark` for dark theme. It cannot call Home Assistant. Set
`JAI_SCREENSHOT_DIR` when running frontend tests to save responsive review PNGs.
Images and development caches are not integration payloads.

## Deployment after approval

1. Review this branch and screenshots, then explicitly approve merge/deployment.
2. Verify a current full HA backup and record current entity IDs, memberships,
   collections, presets, relative masters and dashboard references.
3. Merge normally, preserving history. Install the reviewed commit's complete
   `custom_components/jai_lighting` directory, including all five frontend modules.
   Preserve HA configuration and `.storage`; no migration/reset is required.
4. Restart Core if needed and reload the Manager browser tab to load the updated
   modules. Confirm the installed source commit and absence of integration errors.
5. Inspect existing hierarchy, counts, unavailable lights, identities, master
   values and dashboards without operating lights. Compare the baseline before
   any configuration edit. Confirm startup/editing sends no lighting commands.
6. Test one small existing group first: picker, expansion, assignment/removal,
   Undo, filtered inventory and an intentional two-session revision conflict.
   Restore each test edit, then check phone and tablet scrolling/touch behavior.
   Keep actual lighting controls and preset activation outside this UX check.

No tag/release, visibility change, production deployment or main merge was
performed for this redesign. The annotated `v0.1.0` tag remains immutable.
