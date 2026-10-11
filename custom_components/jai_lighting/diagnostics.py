"""Export only J'ai configuration/outcomes, never legacy backups/Core files."""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    manager = hass.data[DOMAIN]
    async with manager.lock:
        data = manager.snapshot.dump()
        data.pop("runtime")
        return data | {
            "last_operation": manager.outcome,
            "storage_failed": manager.repo.failed,
            "recovery_pending": bool(manager.repo.journal),
        }
