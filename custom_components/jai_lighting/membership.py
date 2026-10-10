"""Physical leaf membership validation shared by flows and runtime."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def is_aggregate(hass: HomeAssistant, entity_id: str) -> bool:
    """Reject known J'ai and built-in groups, including recursive paths."""
    entry = er.async_get(hass).async_get(entity_id)
    state = hass.states.get(entity_id)
    return bool(
        (entry and entry.platform in (DOMAIN, "group"))
        or (state and ("entity_id" in state.attributes or state.attributes.get("jai_group")))
    )


def validate_members(
    hass: HomeAssistant, selected: list[str], existing: list[str]
) -> tuple[list[str], str | None]:
    """Resolve selector UUIDs and retain already configured missing lights."""
    registry = er.async_get(hass)
    members = []
    for reference in selected:
        entity_id = er.async_resolve_entity_id(registry, reference)
        if not entity_id or not entity_id.startswith("light."):
            return [], "invalid_member"
        if (
            entity_id not in existing
            and not hass.states.get(entity_id)
            and not registry.async_get(entity_id)
        ):
            return [], "invalid_member"
        if is_aggregate(hass, entity_id):
            return [], "nested_group"
        if entity_id not in members:
            members.append(entity_id)
    return members, None if members else "no_members"
