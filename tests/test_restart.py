"""Restore off targets from disk into a fresh Home Assistant instance."""

from conftest import create_hass
from homeassistant.helpers.restore_state import async_get as get_restore_data
from test_ha import put


async def test_fresh_ha_instance_restores_master_and_off_target(hass, add_group, tmp_path):
    put(hass, "light.l0", 200, on=False)
    entry = await add_group(["light.l0"])
    await hass.services.async_call(
        "jai_lighting",
        "set_master",
        {"entity_id": "light.jai_sigle_led", "brightness_pct": 50},
        blocking=True,
    )
    assert entry.runtime_data.engine.memory["light.l0"] == 100
    await get_restore_data(hass).async_dump_states()
    await hass.async_stop(force=True)

    restarted = await create_hass(tmp_path)
    try:
        put(restarted, "light.l0", 200, on=False)
        restored_entry = restarted.config_entries.async_get_entry(entry.entry_id)
        assert restored_entry is not None
        assert await restarted.config_entries.async_setup(restored_entry.entry_id)
        await restarted.async_block_till_done()
        assert restored_entry.runtime_data.engine.master == 128
        assert restored_entry.runtime_data.engine.memory["light.l0"] == 100
        assert restarted.states.get("light.jai_sigle_led").state == "off"
    finally:
        await restarted.async_stop(force=True)
