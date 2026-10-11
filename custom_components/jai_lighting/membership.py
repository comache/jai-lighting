"""Physical leaf membership validation shared by flows and runtime."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .models import Identity, ModelError


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


def identity_for(hass: HomeAssistant, reference: str, *, existing=False) -> Identity:
    """Store registry identity; do not route missing registry IDs by old names."""
    registry = er.async_get(hass)
    entry = registry.async_get(reference)
    entity_id = entry.entity_id if entry else reference
    if not entity_id.startswith("light.") or is_aggregate(hass, entity_id):
        raise ModelError("Physical members must be individual light entities")
    if not existing and entry is None and hass.states.get(entity_id) is None:
        raise ModelError("Unknown physical light")
    return Identity("registry", entry.id) if entry else Identity("entity_id_fallback", entity_id)


def resolve_identity(hass: HomeAssistant, identity: Identity) -> str | None:
    if identity.kind == "entity_id_fallback":
        return identity.value
    entry = er.async_get(hass).async_get(identity.value)
    return entry.entity_id if entry and entry.domain == "light" else None
