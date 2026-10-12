# Bubble dashboard diagnostic export

`scripts/export-bubble-diagnostics.sh` is a user-run inspection script for the
Home Assistant Terminal & SSH app. It needs Bash, jq, curl, tar, sha256sum and
mktemp. It installs nothing and makes no configuration changes.

The only network request is a GET of `http://supervisor/core/api/states`, using
the app's existing Supervisor token over stdin. This is the documented Core API
proxy: https://developers.home-assistant.io/docs/apps/communication/ . No service
calls or WebSocket mutations are made. Failure does not change permissions or
try another authentication method. The script reports the missing live states
and still exports the saved configuration.

Run with `bash scripts/export-bubble-diagnostics.sh` in the Terminal app after
copying/downloading the script. It creates a private export directory and one
`.tar.gz` file in `/share`, prints the archive path and SHA256, and lists missing
sources. Share only that archive. It is a diagnostic export, not a backup or a
configuration suitable for restoring into HA.

## Collected information

- Saved Lovelace dashboard index and configurations, including Jai Bistrot.
- J'ai Control's independently saved layout, when one exists.
- J'ai topology, presets, collections, stored relative masters and capabilities.
- Entity, device, area and floor display metadata and stable routing IDs.
- Lovelace resources, HACS custom-card versions, and local card file hashes.
- Spotify entity-registry identification and selected live media capabilities.
- Local HA version and J'ai integration manifest version.

All `.storage` access is read-only. Authentication stores, config entries,
secrets.yaml, restore-state archives, logs and databases are never exported.
Device connections/identifiers, unrelated platform unique IDs, media URLs,
authentication headers and API tokens are excluded. Dashboard free text,
JavaScript/templates, unknown custom fields, external URLs and arbitrary action
payloads are omitted. `_omitted_fields` indicates lossy dashboard filtering;
do not use the result to overwrite an existing dashboard. Relative resource URLs
lose query strings; HACS metadata supplies version evidence separately.

If Mike Farah's yq is already available, the script additionally reads the
`lovelace` section of configuration.yaml, ui-lovelace.yaml and directly referenced
YAML dashboards. It does not execute YAML tags or expand includes. Included
YAML configurations, custom layout code and browser-unsaved edits may require
separate inspection. Missing files, unsaved default layouts, unavailable APIs
and unknown Bubble versions are reported rather than fabricated. Local card
presence is not proof that the browser has loaded that resource.

This is not an atomic snapshot. Avoid editing dashboards/topology while it runs.
Production verification and the separately required full backup remain pending.

## Local verification

`tests/test_bubble_export.py` builds synthetic stores, substitutes a mock HTTP
client, checks API success/refusal, tests credential exclusion and validates
archive checksums and source preservation. No production connection is used.
Test-only environment overrides are `JAI_EXPORT_CONFIG_DIR`,
`JAI_EXPORT_OUTPUT_DIR` and `JAI_EXPORT_OFFLINE=1`.
