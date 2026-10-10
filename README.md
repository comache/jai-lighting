# J'ai Lighting

A Home Assistant custom integration for relative lighting-group master faders.
Create `light.jai_sigle_led` from existing physical lights and use it in dashboards,
scenes and automations. Change its membership through the UI when replacing a
light; the virtual entity keeps its identity.

Targets **Home Assistant Core 2026.10.x**, including installations on **HA OS
18.3**. Development uses Python >=3.14.2 and the exact Core 2026.10.0 runtime.
This integration is an initial release: test it with a small group on the actual
installation before rolling it out to all 30+ lights.

## Install

### Manual installation

1. Copy `custom_components/jai_lighting/` from this repository into
   `/config/custom_components/jai_lighting/` on Home Assistant. The manifest must
   be at `/config/custom_components/jai_lighting/manifest.json`.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → J'ai Lighting**.
4. Enter a group name and select physical `light.*` members.

No YAML or helper setup is required. Existing physical lights remain directly
controllable.

### GitHub repository and HACS metadata

The project repository is [comache/jai-lighting](https://github.com/comache/jai-lighting)
and remains private. The owner and repository address have been verified through
the connected GitHub plugin. Documentation and issue links use that repository;
the manifest maintainer is `@comache` and the integration version remains `0.1.0`.

The repository has the HACS directory structure, `hacs.json`, and a versioned
manifest. HACS requires public repositories, so this private baseline is intended
for manual installation. It is not a publicly installable HACS repository.
See the [HACS requirements](https://www.hacs.xyz/docs/publish/start/).

If you later choose to publish the repository publicly, add
`https://github.com/comache/jai-lighting` under HACS **Custom repositories** with
category **Integration**, download it, restart HA, and add the integration in
Settings. Public publication and a GitHub Release require a separate decision.

## Configure groups

For the example installation, create **Sigle LED** with:

- `light.led_perete`
- `light.semn_led_jai_bar`
- `light.semn_led_jai_spate`

Its suggested entity ID is `light.jai_sigle_led`. Home Assistant may add a suffix
if that ID already exists. Rename the entity ID once in its entity settings if
needed; later group-name or membership edits keep the registered entity ID.

Use the integration entry's **Configure** options to add, remove or replace
members. Repeating **Add integration** creates other independent groups, such as
Hol Sus, Backbar, Fatada, Interior or Terasa. A replacement uses its own level;
it does not inherit the removed light's level. Removing a member does not turn it
off. After renaming a physical entity ID, update its membership in Configure.

Known J'ai groups and built-in aggregate groups are excluded/rejected. Select
physical leaves only. Nested groups are disabled in this release.

## Master behavior

The master is an independent fader, initially 100%, rather than an average of its
members. Moving it from 50% to 60% multiplies each currently on, dimmable member's
**real reported brightness** by `60 / 50`. A member at 60% becomes about 72%;
a member already at 100% stays at 100%.

Clipping permanently loses the excess. Starting with master 50 and members
100/60, the sequence 60 → 30 → 90 → 50 produces approximately:

| Master | Member 1 | Member 2 |
| --- | --- | --- |
| 50 | 100 | 60 |
| 60 | 100 | 72 |
| 30 | 50 | 36 |
| 90 | 100 | 100 |
| 50 | 55.6 | 55.6 |

There are no hidden values above 100%, trim helpers, or automatic changes to
other members when a physical light is adjusted manually. The next fader move
uses that light's current level. Lights individually switched off stay off
during an active master move.

HA uses integer brightness bytes. The master range is 3..255 (approximately
1.18..100%, the first byte at or above 1%); physical outputs are 1..255.
Ratios and half-up rounding operate on those bytes. Very small levels and
repeated changes therefore have ordinary quantization loss. Colors and color
temperatures are preserved; color controls and transitions are not exposed.

## Power and off-state adjustment

The normal light toggle turns every available member off/on. Off retains the
master and saved levels. On powers on all available members, including those
previously turned off individually, using their last observed levels (or their
off brightness attribute, otherwise 100% on first use).

**Home Assistant's normal brightness slider turns a light on.** Use
**Developer tools → Actions → J'ai Lighting: Set master brightness** to move the
master while keeping the group off. Its entity selector and slider are native
Home Assistant UI controls. This action also works while the group is on:

```yaml
action: jai_lighting.set_master
target:
  entity_id: light.jai_sigle_led
data:
  brightness_pct: 35
```

For master 70 and levels 30/50/100: turn off, set master to 35, then turn on to
restore about 15/25/50. Multiple off-state changes apply sequentially, clipping
at each step just as they would while on. No commands reach physical lights
during these off-state master changes.

Standard `light.turn_on` with brightness both moves the master and turns the
members on. A brightness-zero light command follows HA's normal off behavior
and retains the master. The `set_master` action accepts only 1..100 percent.
The ordinary HA light brightness attribute is null while off; use the
`master_brightness` or `master_brightness_pct` attributes to inspect the retained
fader position.

## Availability, failure and restart behavior

The group is on when any available member is on, and available when at least one
member reports on/off. Missing, unknown and unavailable members are skipped.
`unavailable_members` and `command_failures` attributes identify affected lights.
On/off-only lights participate in power controls and are left unchanged by the
fader. An on dimmable light that does not report brightness is skipped during a
master move until it reports a real baseline.

Calls inherit the originating HA context. Overlapping groups serialize their
commands. The integration waits for real state reports, allowing up to two
seconds after a physical service returns; the service itself has a ten-second
timeout. Device rounding is accepted. Failures attempt the other members,
retain the requested master and raise an action error listing affected lights.
There are no automatic retries or deferred power-on commands for members that
later reconnect. A recovered light's next actual on report becomes its baseline.
Rapid hardware updates or concurrent external automations can still race an
operation; arbitrary physical devices do not provide transactions.

Master and bounded restore targets survive normal integration reloads and HA
restarts through `RestoreEntity` extra data. Startup sends no light commands;
current on levels override stored ones. HA saves restore data periodically and
on graceful shutdown, so sudden power loss can lose recent fader changes.

## Development and validation

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Tests cover ratio increases/decreases, clipping loss, manual baselines, off/on,
multiple off changes, unavailable and replaced members, persistence and mixed
capabilities. Runtime tests use real HA flows, LightEntity services and registry
lifecycle; fake physical lights supply device reports. GitHub Actions runs the
same suite on Linux with Python 3.14. Tests do not verify the actual bistrot
devices, HA OS image, or network transport latency.

See [ARCHITECTURE.md](ARCHITECTURE.md) for API verification, state decisions,
failure semantics and the future deduplicated-leaf topology design.
