// Pure operational view/command planning. No calls occur while resolving state.
export const palette = {
  lavender: "#c8b6ff",
  peach: "#ffcfaf",
  mint: "#b5dfcb",
  blue: "#adcfff",
  rose: "#f5b6ce",
};
export const keyOf = (ref) => (ref ? `${ref.kind}:${ref.id}` : "");
export const refOf = (key) => {
  const i = key.indexOf(":");
  return i < 1 ? null : { kind: key.slice(0, i), id: key.slice(i + 1) };
};
export const emptyCatalog = () => ({
  groups: {},
  entities: {},
  collections: {},
  presets: {},
  floors: [],
  areas: [],
});
export function resolve(ref, catalog, states) {
  if (!ref)
    return {
      name: "Neconfigurat",
      available: false,
      brightness: false,
      power: false,
    };
  const record =
    catalog[
      {
        group: "groups",
        entity: "entities",
        collection: "collections",
        preset: "presets",
      }[ref.kind]
    ]?.[ref.id];
  const entityId = ref.kind === "entity" ? ref.id : record?.entity_id;
  const state = states[entityId],
    attrs = state?.attributes || {};
  const groupId =
    ref.kind === "group"
      ? ref.id
      : record?.group_id || (attrs.jai_group ? attrs.group_id : null);
  if (ref.kind === "collection") {
    const all = (record?.entities || []).map((id) => states[id]);
    const live = all.filter(
      (s) => s && !["unavailable", "unknown"].includes(s.state),
    );
    const dim = live.filter((s) =>
      (s.attributes.supported_color_modes || []).some(
        (m) => !["onoff", "unknown"].includes(m),
      ),
    );
    const on = live.filter((s) => s.state === "on").length;
    return {
      ref,
      name: record?.name || "Selecție indisponibilă",
      available: live.length > 0,
      power: live.length > 0,
      on: on > 0,
      mixed: on > 0 && on < live.length,
      brightness: dim.length > 0,
      pct: dim.length
        ? Math.round(
            (dim.reduce((n, s) => n + (s.attributes.brightness || 0), 0) /
              dim.length /
              255) *
              100,
          )
        : 0,
      missing: all.length - live.length,
      absolute: true,
      domain: "collection",
    };
  }
  const domain = entityId?.split(".")[0];
  const available =
    !!state &&
    state.state !== "unavailable" &&
    (domain === "scene" || state.state !== "unknown");
  const modes = attrs.supported_color_modes || [];
  const brightness =
    domain === "light" && modes.some((m) => !["onoff", "unknown"].includes(m));
  return {
    ref,
    entityId,
    groupId,
    domain,
    state: state?.state,
    attrs,
    name: record?.name || attrs.friendly_name || ref.id,
    available,
    power: available && ["light", "switch", "input_boolean"].includes(domain),
    on: state?.state === "on",
    mixed:
      !!groupId &&
      (attrs.entity_id || []).some((id) => states[id]?.state === "on") &&
      (attrs.entity_id || []).some((id) => states[id]?.state === "off"),
    brightness,
    pct: Math.round(
      groupId
        ? (attrs.master_brightness_pct ??
            ((attrs.master_brightness ?? 255) / 255) * 100)
        : ((attrs.brightness || 0) / 255) * 100,
    ),
    missing: attrs.unavailable_members?.length || 0,
  };
}
export function commandFor(ref, action, value, catalog, states) {
  const view = resolve(ref, catalog, states);
  if (!view.available) throw new Error("Entitatea nu este disponibilă.");
  if (action === "brightness" && !view.brightness)
    throw new Error("Luminozitatea nu este suportată.");
  if (action === "power" && !view.power)
    throw new Error("Comanda de alimentare nu este suportată.");
  if (
    action === "brightness" &&
    (!Number.isFinite(value) || value < (view.groupId ? 1 : 0) || value > 100)
  )
    throw new Error("Valoare de luminozitate invalidă.");
  if (ref.kind === "collection")
    return {
      type: "jai_lighting/dashboard",
      command: "collection",
      collection_id: ref.id,
      properties:
        action === "power"
          ? { power: view.on ? "off" : "on" }
          : { brightness_pct: value },
    };
  if (action === "brightness" && view.groupId)
    return {
      domain: "jai_lighting",
      service: "set_master",
      data: { entity_id: view.entityId, brightness_pct: value },
    };
  if (action === "brightness")
    return {
      domain: "light",
      service: "turn_on",
      data: { entity_id: view.entityId, brightness_pct: value },
    };
  if (action === "power")
    return {
      domain: view.domain,
      service: view.on ? "turn_off" : "turn_on",
      data: { entity_id: view.entityId },
    };
  if (action === "mood" && ref.kind === "preset")
    return {
      domain: "jai_lighting",
      service: "activate_preset",
      data: { entity_id: view.entityId },
    };
  const features = view.attrs.supported_features || 0;
  const media = {
    play: ["media_play", 16384],
    pause: ["media_pause", 1],
    previous: ["media_previous_track", 16],
    next: ["media_next_track", 32],
    volume: ["volume_set", 4],
  };
  if (
    view.domain === "media_player" &&
    media[action] &&
    features & media[action][1]
  )
    return {
      domain: "media_player",
      service: media[action][0],
      data: {
        entity_id: view.entityId,
        ...(action === "volume"
          ? { volume_level: Math.max(0, Math.min(1, value / 100)) }
          : {}),
      },
    };
  throw new Error("Acțiune indisponibilă pentru această entitate.");
}
export function childrenOf(id, catalog) {
  const group = catalog.groups[id];
  return group
    ? [
        ...group.children.map((id) => ({ kind: "group", id })),
        ...group.lights.map((id) => ({ kind: "entity", id })),
      ]
    : [];
}
export function reorder(items, id, before) {
  const item = items.find((x) => x.id === id);
  if (!item || id === before) return items;
  const out = items.filter((x) => x.id !== id);
  const at = out.findIndex((x) => x.id === before);
  out.splice(at < 0 ? out.length : at, 0, item);
  return out;
}
export function safeArtwork(value) {
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value, location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
