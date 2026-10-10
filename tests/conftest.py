"""Use the real pinned HA runtime, without replacing HA APIs with stubs."""

from pathlib import Path

import pytest
from homeassistant import loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

COMPONENTS = Path(__file__).resolve().parents[1] / "custom_components"


async def create_hass(config_dir):
    """Start HA's real base registries and light component for one instance."""
    instance = HomeAssistant(str(config_dir))
    instance.config.skip_pip = True
    loader.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    assert await async_load_base_functionality(instance)
    assert await async_setup_component(instance, "light", {})
    return instance


@pytest.fixture
async def hass(tmp_path):
    (tmp_path / "custom_components").symlink_to(COMPONENTS, target_is_directory=True)
    instance = await create_hass(tmp_path)
    yield instance
    await instance.async_stop(force=True)


def make_entry(members, name="Sigle LED"):
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain="jai_lighting",
        title=name,
        data={"name": name, "members": members},
        options={},
        source="user",
        unique_id=None,
        discovery_keys={},
        subentries_data=[],
    )


@pytest.fixture
async def add_group(hass):
    async def add(members, name="Sigle LED"):
        entry = make_entry(members, name)
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        assert entry.state.value == "loaded"
        return entry

    return add
