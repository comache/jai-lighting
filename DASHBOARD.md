# J'ai Control

An operational dashboard and administrator Designer inside Home Assistant. It uses
HA's supported custom-panel mechanism at `/jai-control`; there is no second web
server, login or external frontend dependency. The same module also exports the
reusable `custom:jai-control-card` for a full-width Lovelace panel view. Existing
Lovelace dashboards, resources and Bubble Cards are never edited by the integration.

The shell has its own permanent sidebar, clock/date (Romanian, Europe/Bucharest),
independent content scrolling, sticky tabs, responsive cards and recursive details.
The primary review viewport is 1024 × 768, with additional landscape, portrait,
phone, Chromium and WebKit automated coverage. Synthetic screenshots and fixtures
are examples, not evidence of production deployment.

## Discovery and first layout

The dashboard reads actual HA states and registries through its authenticated
`jai_lighting/dashboard` WebSocket API. It discovers floors, areas, enabled entities,
J'ai groups, collections, preset scenes and Spotify platform entities. Group IDs
come from the entity registry; renaming a group or its entity does not change the
stored dashboard binding. Generic controls use explicit HA entity IDs.

An unsaved initial suggestion uses real floors and group ancestry/name matches to
place relevant groups. Empty floors remain empty. Unambiguous groups named Interior
and Service populate their corresponding sidebar slots. A single discovered Spotify
player is proposed; ambiguous or missing players require a choice in the Designer.
Shortcuts and moods start unselected so unrelated switches or presets are not
silently assigned. No fictitious sensor data or replacement devices are created.

Open **Designer → Sidebar** to review the proposed bindings and choose quick
switches. Review each tab, then **Salvează design**. This is the only operation that
persists the initial layout. Later discovery does not overwrite a saved layout.
A group can appear on several tabs or as a quick control at any depth. This changes
only dashboard placement. Service obeys the existing physical hierarchy: if it is
already under Interior, operating Interior still includes it.

## Operating controls

- Group brightness uses `jai_lighting.set_master` (1–100%), including virtual
  groups selected through the generic entity picker. Moving it while OFF preserves
  power. Separate ON/OFF buttons use native light services on one target.
- Individual dimmable lights use native brightness services. These can turn a
  physical light on. Binary lights expose power only.
- Sliders preview during dragging and commit on release. Pending final changes to
  the same target are coalesced. A failed command is shown and never retried on
  reconnection. Power and preset buttons are explicit actions.
- Collections are **Selecții speciale**, with absolute power/brightness. They do
  not acquire relative masters. The server checks control permission on every
  physical target and resolves a fixed leaf selection before entering the engine.
- Moods reference real preset identities and activate through the existing domain
  action. An unactivated HA scene with state `unknown` remains selectable; missing
  or unavailable scenes do not.
- Spotify uses the existing media player, feature flags, artwork, track/artist,
  transport, volume and current source. An inactive device is explained rather than
  treated as HA audio output. Select a playback device in Spotify Connect.
- Group details show only immediate child groups and direct physical lights.
  Breadcrumbs navigate recursively, with no per-group popup configuration. Physical
  entities can open HA's own more-info dialog.

Starting, reconnecting, browsing or editing a layout issues no physical commands.
Designer mode disables operational controls. It never changes groups, collections,
preset recipes, registries, retained levels or lighting membership.

## Designer and persistence

Administrators can create, rename, reorder and hide tabs; add, edit, remove and
reorder cards; choose size, pastel accent and MDI icon; choose arbitrary discovered
targets; set quick controls and optional compact brightness; select tab moods; and
configure the sidebar. Card dragging is supplemented by arrow buttons for keyboard
and touch accessibility. At least one tab must remain visible.

Edits remain in preview until **Salvează design**. **Anulează** discards the draft.
The independent version-1 HA Store is `.storage/jai_lighting.dashboard`. Saves are
awaited, revision-checked and protected against concurrent edits. A failed write
keeps the previous durable layout. Corrupt or unsupported stored data is retained
and reported, never silently reset. Missing references remain visible to an admin
for repair. Operational readers see only permitted bindings.

The API supports `get`, administrator-only `save`, and permission-checked explicit
`collection` controls. The original Manager API stays administrator-only. Standard
light, media, switch and preset actions continue through HA's service permissions.
No extra credential, authentication bypass or persistent access grant is introduced.

## Dedicated iPad / kiosk use

1. Sign in to the existing HA instance with the intended operational account and
   open `/jai-control` directly. Use a separate administrator session for maintenance.
2. Collapse HA navigation with its menu button. **Ecran complet** requests browser
   fullscreen for this dashboard only, hiding HA chrome while active. It requires a
   user gesture and browser support; exiting fullscreen restores normal navigation.
3. In iPad Safari, use Share → Add to Home Screen to open the dashboard directly.
   If fullscreen is unavailable in that browser/container, the dashboard reports
   that limitation. Its own navigation button remains accessible on small screens.
4. Optional [Apple Guided Access](https://support.apple.com/en-us/111795) can keep
   the dedicated iPad in the selected app. Configure and retain the exit method
   yourself; the integration does not lock the device or alter security settings.

Navigation visibility is not a security boundary. No global sidebar modification,
CSS injection into HA internals or administrator navigation setting is installed.
Actual Safari/iPad Home Screen fullscreen behavior needs owner acceptance on the
physical iPad; automated WebKit tests are not physical-device certification.

## Reusable Lovelace card

The automatic `/jai-control` panel requires no YAML or resource entry. For an
additional manually created Lovelace dashboard, register
`/jai_lighting_static/control.js` as a JavaScript module resource and use:

```yaml
type: custom:jai-control-card
```

Use a panel view for full-width operation. This card uses the same independent
layout Store and Designer. Do not replace an existing dashboard to install it.

Supported HA frontend APIs:
[custom panels](https://developers.home-assistant.io/docs/frontend/custom-ui/creating-custom-panels/),
[custom cards](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/).
The implementation is validated against the installed pinned HA 2026.10.0 runtime.

## Validation and safe production installation

```sh
.venv/bin/python -X faulthandler -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npm run format:check
npm run test:frontend
npm run test:control:webkit
```

Install Playwright engines with `npx playwright install chromium webkit`. On a
Mac with existing Chrome, `JAI_BROWSER_CHANNEL=chrome npm run test:frontend` uses
that executable with an isolated test profile. All automated devices are synthetic.
`npm run preview:control` serves a loopback-only, explicitly synthetic preview.

Before production file changes, create a **new full HA backup**, verify its archive
and nested archives, and record the filename, timestamp, location, Core version and
previous source commit. Stop if verification fails. Capture the current hierarchy,
entity IDs, masters, collections, presets, existing dashboards and automations for
comparison. Keep these private observations outside Git.

Stage the exact reviewed Git commit, verify its source hash, install only the
integration source, run HA configuration validation and restart Core if required.
`J'ai Control` appears as a new sidebar destination, alongside the existing Manager.
Use the discovered production catalog to review and save the new dashboard layout.
Compare all existing configuration and IDs with the pre-deployment snapshot.
Check Core/browser logs, assets, sidebar, clock, tabs, recursive popup, Designer
save, unavailable states and the old dashboard. Do not operate lights, switches,
Spotify or moods during automated deployment verification.

If installation fails, preserve logs and stop changes. Restore the complete matching
backup and previous source when needed; do not reset groups or perform an unsafe
code-only downgrade after configuration changes.

Owner live acceptance: begin with one small group, confirm OFF-safe master movement,
then explicitly test ON/OFF, relative balance, a physical light, an existing mood,
a special selection and Spotify Connect. Check touch dragging/fullscreen on the real
iPad. These are intentionally excluded from automated production verification.
