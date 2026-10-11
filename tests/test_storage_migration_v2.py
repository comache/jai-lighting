"""Disk persistence, failure safety, and deterministic legacy conversion."""

from copy import deepcopy

import pytest

from custom_components.jai_lighting.migration import OwnershipRequired, preflight
from custom_components.jai_lighting.models import Identity, ModelError
from custom_components.jai_lighting.storage import Repository, Snapshot


def entry(id_, members):
    return {"entry_id": id_, "data": {"name": "User group", "members": members}, "options": {}}


def converted():
    return preflight(
        [entry("g", ["light.sample"])],
        {"g": {"version": 1, "master": 90, "memory": {"light.sample": 60}}},
        {"light.sample": Identity("registry", "stable")},
    )


async def test_real_ha_store_round_trip_without_services(hass):
    repo = Repository(hass)
    await repo.load()
    await repo.save(converted())
    restarted = Repository(hass)
    await restarted.load()
    assert restarted.snapshot.dump() == repo.snapshot.dump()
    assert restarted.snapshot.runtime.masters == {"g": 90}


def test_migration_identity_bounded_memory_and_original_input_untouched():
    original = [entry("g", ["light.sample", "light.missing"])]
    copy = deepcopy(original)
    result = preflight(
        original,
        {"g": {"version": 1, "master": 90, "memory": {"light.sample": 60, "removed": 200}}},
        {"light.sample": Identity("registry", "stable")},
    )
    assert original == copy
    assert result.tree.groups["g"].parent is None
    assert result.tree.groups["g"].policy == "RESTORE"
    assert list(result.runtime.targets.values()) == [60]
    assert len(result.tree.leaves) == 2
    assert result.tree.leaves[result.tree.resolve("g")[0]].identity.value == "stable"


def test_overlap_requires_owner_and_chosen_owner_memory():
    entries = [entry("a", ["light.sample"]), entry("b", ["light.sample"])]
    restored = {
        "a": {"version": 1, "master": 100, "memory": {"light.sample": 40}},
        "b": {"version": 1, "master": 200, "memory": {"light.sample": 80}},
    }
    with pytest.raises(OwnershipRequired) as error:
        preflight(entries, restored, {})
    assert error.value.overlaps == {"entity_id_fallback:light.sample": ["a", "b"]}
    result = preflight(entries, restored, {}, {"entity_id_fallback:light.sample": "b"})
    assert result.tree.resolve("a") == ()
    assert result.runtime.masters == {"a": 100, "b": 200}
    assert list(result.runtime.targets.values()) == [80]


async def test_journal_prepare_reload_finish_and_abort(hass):
    repo = Repository(hass)
    await repo.prepare(
        converted(),
        [{"id": "g", "kind": "group"}],
        kind="migration",
        legacy={"entries": [entry("g", ["light.sample"])]},
    )
    assert not repo.snapshot.tree.groups
    restarted = Repository(hass)
    await restarted.load()
    assert restarted.journal["phase"] == "prepared"
    await restarted.finish()
    assert restarted.snapshot.runtime.masters["g"] == 90
    assert restarted.legacy["entries"][0]["data"]["members"] == ["light.sample"]
    await restarted.prepare(Snapshot(), [])
    await restarted.abort()
    assert restarted.snapshot.runtime.masters["g"] == 90


async def test_failed_save_does_not_publish_candidate(hass, monkeypatch):
    repo = Repository(hass)

    async def fail(data):
        raise OSError("disk failure")

    monkeypatch.setattr(repo.store, "async_save", fail)
    with pytest.raises(OSError):
        await repo.save(converted())
    assert not repo.snapshot.tree.groups
    assert repo.failed


def test_future_schema_and_invalid_state_are_not_defaulted():
    data = converted().dump()
    data["schema_version"] = 99
    with pytest.raises(ModelError, match="Unsupported"):
        Snapshot.load(data)
    data = converted().dump()
    data["runtime"]["group_masters"]["g"] = 0
    with pytest.raises(ModelError):
        Snapshot.load(data)


@pytest.mark.parametrize("text", ["{broken", "[]", '{"version": 99, "data": {}}'])
async def test_corrupt_or_future_store_file_preserved(hass, text):
    from pathlib import Path

    repo = Repository(hass)
    path = Path(repo.store.path)
    path.parent.mkdir(exist_ok=True)
    await hass.async_add_executor_job(path.write_text, text)
    with pytest.raises(ModelError):
        await repo.load()
    assert await hass.async_add_executor_job(path.read_text) == text
    assert not list(path.parent.glob("*.corrupt*"))


def test_raw_legacy_backup_excludes_other_integrations_and_preserves_record_bytes():
    from custom_components.jai_lighting.migration import raw_legacy_records

    original = '{ "entry_id" : "g", "data": {"name":"User’s lamp"}, "options": {} }'
    text = (
        '{"data":{"entries":[' + original + ',{"entry_id":"other","data":{"token":"unrelated"}}]}}'
    )
    assert raw_legacy_records(text, {"g"}) == {"g": original}
    original = '{"state": {"entity_id": "light.jai_example"}, "extra_data": {"master": 90}}'
    assert raw_legacy_records(
        '{"data":[' + original + "]}", {"light.jai_example"}, restore=True
    ) == {"light.jai_example": original}


async def test_legacy_backup_is_never_overwritten(hass):
    repo = Repository(hass)
    await repo.prepare(
        Snapshot(),
        [],
        legacy={"entries": [entry("g", ["light.old"])], "raw_entries": {"g": "original"}},
    )
    await repo.finish()
    await repo.prepare(
        Snapshot(),
        [],
        legacy={"entries": [entry("g", ["light.new"])], "raw_entries": {"g": "replacement"}},
    )
    assert repo.legacy["entries"][0]["data"]["members"] == ["light.old"]
    assert repo.legacy["raw_entries"]["g"] == "original"
