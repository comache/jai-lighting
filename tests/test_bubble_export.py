"""Exercise the user-run export in an isolated, synthetic HA directory."""

import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/export-bubble-diagnostics.sh"


@pytest.mark.parametrize("api_ok", [True, False])
def test_export_is_read_only_and_does_not_package_credentials(tmp_path, api_ok):
    config, output, binaries = (tmp_path / p for p in ("config", "output", "bin"))
    for directory in (config / ".storage", output, binaries):
        directory.mkdir(parents=True)

    def store(name, data):
        (config / ".storage" / name).write_text(json.dumps({"data": data}))

    secret = "DO_NOT_EXPORT_CREDENTIAL"
    store("auth", {"refresh_token": secret})
    store("core.config_entries", {"data": {"password": secret}})
    (config / "secrets.yaml").write_text(f"password: {secret}")
    store(
        "core.entity_registry",
        {
            "entities": [
                {
                    "id": "registry-id",
                    "entity_id": "light.test_group",
                    "platform": "jai_lighting",
                    "unique_id": "group-id",
                    "options": {"password": secret},
                },
                {"entity_id": "media_player.music", "platform": "spotify", "unique_id": secret},
            ]
        },
    )
    store("core.device_registry", {"devices": [{"id": "device", "connections": [secret]}]})
    store("core.area_registry", {"areas": [{"id": "area", "name": "Test area"}]})
    store("core.floor_registry", {"floors": [{"id": "floor", "name": "Test floor"}]})
    store(
        "jai_lighting.state",
        {
            "groups": {"group-id": {"name": "Test group"}},
            "leaves": {"leaf": {"identity": {"kind": "registry", "value": "registry-id"}}},
            "presets": {"preset": {"name": "Test preset"}},
            "collections": {"collection": {"name": "Test collection", "members": ["leaf"]}},
            "journal": {"token": secret},
            "legacy_backup": {"raw_entries": secret},
        },
    )
    store("jai_lighting.dashboard", {"revision": 1, "config": {"title": "J'ai Control"}})
    store("lovelace_dashboards", {"items": [{"id": "bistrot", "title": "Jai Bistrot"}]})
    store(
        "lovelace.bistrot",
        {
            "config": {
                "title": "Jai Bistrot",
                "views": [
                    {
                        "cards": [
                            {
                                "type": "custom:bubble-card",
                                "entity": "light.test_group",
                                "content": secret,
                                "custom_unknown": secret,
                                "tap_action": {
                                    "action": "call-service",
                                    "service": "jai_lighting.set_master",
                                    "data": {"brightness_pct": 45, "password": secret},
                                },
                                "url": f"https://user:{secret}@example.invalid/image",
                            }
                        ]
                    }
                ],
            }
        },
    )
    store(
        "lovelace_resources",
        {
            "items": [
                {"url": f"/hacsfiles/bubble-card/bubble-card.js?token={secret}", "type": "module"}
            ]
        },
    )
    store(
        "hacs.repositories",
        {
            "repositories": [
                {
                    "full_name": "Clooos/Bubble-Card",
                    "category": "plugin",
                    "installed_version": "v3.4.1",
                    "token": secret,
                }
            ]
        },
    )
    (config / ".HA_VERSION").write_text("2026.10.0\n")
    (config / "www/community/bubble-card").mkdir(parents=True)
    (config / "www/community/bubble-card/bubble-card.js").write_text("// synthetic card")
    # The only network tool is replaced: no access to any live HA or device.
    (binaries / "curl").write_text(
        "#!/bin/bash\n"
        '[[ "$*" == *"--request GET http://supervisor/core/api/states"* ]] || exit 90\n'
        '[[ "$*" != *"DO_NOT_EXPORT"* ]] || exit 91\n'
        "cat >/dev/null\n"
        + (
            "cat <<'JSON'\n"
            + json.dumps(
                [
                    {
                        "entity_id": "media_player.music",
                        "state": "idle",
                        "attributes": {
                            "friendly_name": "Spotify",
                            "access_token": secret,
                            "media_content_id": secret,
                        },
                    },
                    {
                        "entity_id": "light.test_group",
                        "state": "off",
                        "attributes": {"master_brightness_pct": 45},
                    },
                    {"entity_id": "sensor.password", "state": secret, "attributes": {}},
                ]
            )
            + "\nJSON\n"
            if api_ok
            else f"echo '{secret}' >&2\nexit 22\n"
        )
    )
    (binaries / "curl").chmod(0o700)
    # Force optional YAML support absent, regardless of developer workstation.
    (binaries / "yq").write_text("#!/bin/sh\nexit 1\n")
    (binaries / "yq").chmod(0o700)
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in config.rglob("*") if p.is_file()}
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "JAI_EXPORT_CONFIG_DIR": str(config),
            "JAI_EXPORT_OUTPUT_DIR": str(output),
            "SUPERVISOR_TOKEN": secret,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert secret not in result.stdout + result.stderr
    assert before == {
        p: hashlib.sha256(p.read_bytes()).digest() for p in config.rglob("*") if p.is_file()
    }
    archive = next(output.glob("*.tar.gz"))
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as bundle:
        contents = {
            member.name.split("/", 1)[1]: bundle.extractfile(member).read().decode()
            for member in bundle.getmembers()
            if member.isfile()
        }
    assert secret not in "".join(contents.values())
    assert "auth" not in contents and "core.config_entries" not in contents
    assert json.loads(contents["bubble-version.json"])[0]["installed_version"] == "v3.4.1"
    assert json.loads(contents["entities.json"])[0]["unique_id"] == "group-id"
    assert json.loads(contents["spotify-entities.json"])[0]["entity_id"] == "media_player.music"
    assert "group-id" in json.loads(contents["jai-topology.json"])["groups"]
    assert json.loads(contents["jai-control.json"])["config"]["title"] == "J'ai Control"
    assert "light.test_group" in contents["dashboards/lovelace.bistrot.json"]
    assert (
        json.loads(contents["resources.json"])[0]["url"] == "/hacsfiles/bubble-card/bubble-card.js"
    )
    assert ("live-states.json" in contents) == api_ok
    for line in contents["SHA256SUMS"].splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256(contents[name.removeprefix("./")].encode()).hexdigest() == digest
