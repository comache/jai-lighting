"""J'ai Lighting virtual relative master faders."""

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_MEMBERS, DOMAIN
from .coordinator import GroupCoordinator

PLATFORMS = [Platform.LIGHT]
type JaiConfigEntry = ConfigEntry[GroupCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: JaiConfigEntry) -> bool:
    """Set up one group using a shared lock for overlapping membership."""
    lock = hass.data.setdefault(DOMAIN, asyncio.Lock())
    config = entry.options or entry.data
    entry.runtime_data = GroupCoordinator(hass, config[CONF_MEMBERS], lock)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: JaiConfigEntry) -> None:
    """Reload without changing the stable virtual entity unique ID."""
    config = entry.options or entry.data
    hass.config_entries.async_update_entry(entry, title=config[CONF_NAME])
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JaiConfigEntry) -> bool:
    """Remove entities and their listeners."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
