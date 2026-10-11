// Pure view helpers. The server remains authoritative for topology and control.
export function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}

export function siblingIds(data, kind, parent) {
  const records = kind === "group" ? data.groups : data.leaves;
  return Object.keys(records)
    .filter((id) => records[id].parent_group_id === parent)
    .sort(
      (a, b) =>
        records[a].sibling_order - records[b].sibling_order ||
        a.localeCompare(b),
    );
}

// Index each snapshot once per traversal rather than scanning all records per node.
function hierarchyIndex(data) {
  const index = { group: new Map(), leaf: new Map() };
  for (const kind of ["group", "leaf"]) {
    const records = kind === "group" ? data.groups : data.leaves;
    for (const [id, record] of Object.entries(records)) {
      const children = index[kind].get(record.parent_group_id) || [];
      children.push(id);
      index[kind].set(record.parent_group_id, children);
    }
    for (const children of index[kind].values())
      children.sort(
        (a, b) =>
          records[a].sibling_order - records[b].sibling_order ||
          a.localeCompare(b),
      );
  }
  return (kind, parent) => index[kind].get(parent) || [];
}

export function treeRows(data, expanded, query = "") {
  const siblings = hierarchyIndex(data),
    lights = new Map(
      data.inventory.lights.map((light) => [light.entity_id, light]),
    );
  const rows = [],
    stack = siblings("group", null)
      .reverse()
      .map((id) => ({ kind: "group", id, depth: 0 }));
  const needle = query.trim().toLocaleLowerCase();
  // Filtering keeps matching descendants and their ancestors visible.
  const matches = new Set();
  if (needle) {
    for (const [id, leaf] of Object.entries(data.leaves)) {
      const light = lights.get(data.routes[id]);
      if (
        `${light?.name ?? ""} ${data.routes[id] ?? id}`
          .toLocaleLowerCase()
          .includes(needle)
      ) {
        matches.add(`leaf:${id}`);
        let parent = leaf.parent_group_id;
        while (parent) {
          matches.add(`group:${parent}`);
          parent = data.groups[parent].parent_group_id;
        }
      }
    }
    for (const [id, group] of Object.entries(data.groups)) {
      if (group.name.toLocaleLowerCase().includes(needle)) {
        matches.add(`group:${id}`);
        let parent = group.parent_group_id;
        while (parent) {
          matches.add(`group:${parent}`);
          parent = data.groups[parent].parent_group_id;
        }
      }
    }
  }
  while (stack.length) {
    const row = stack.pop();
    if (!needle || matches.has(`${row.kind}:${row.id}`)) rows.push(row);
    if (row.kind === "group" && (needle || expanded.has(row.id))) {
      const children = siblings("group", row.id).map((id) => ({
        kind: "group",
        id,
        depth: row.depth + 1,
      }));
      const leaves = siblings("leaf", row.id).map((id) => ({
        kind: "leaf",
        id,
        depth: row.depth + 1,
      }));
      stack.push(...[...children, ...leaves].reverse());
    }
  }
  return rows;
}

export function filterLights(inventory, filters) {
  const query = (filters.query || "").toLocaleLowerCase();
  return inventory.lights.filter(
    (light) =>
      (!query ||
        `${light.name} ${light.entity_id}`
          .toLocaleLowerCase()
          .includes(query)) &&
      (!filters.area_id || light.area_id === filters.area_id) &&
      (!filters.device_id || light.device_id === filters.device_id) &&
      (!filters.label || light.labels.includes(filters.label)),
  );
}

export function reorderOperation(data, kind, id, offset) {
  const record = (kind === "group" ? data.groups : data.leaves)[id];
  const ids = siblingIds(data, kind, record.parent_group_id);
  const from = ids.indexOf(id),
    to = from + offset;
  if (to < 0 || to >= ids.length) return null;
  ids.splice(from, 1);
  ids.splice(to, 0, id);
  return { action: "reorder", kind, parent: record.parent_group_id, ids };
}

export function dropOperations(data, items, target, before = false) {
  if (
    before &&
    items.length === 1 &&
    items[0].kind === target.kind &&
    items[0].id !== target.id
  ) {
    const kind = target.kind,
      records = kind === "group" ? data.groups : data.leaves;
    if (
      records[items[0].id].parent_group_id ===
      records[target.id].parent_group_id
    ) {
      const parent = records[target.id].parent_group_id,
        ids = siblingIds(data, kind, parent);
      ids.splice(ids.indexOf(items[0].id), 1);
      ids.splice(ids.indexOf(target.id), 0, items[0].id);
      return [{ action: "reorder", kind, parent, ids }];
    }
  }
  if (target.kind === "leaf") {
    return [
      { action: "move", items, parent: data.leaves[target.id].parent_group_id },
    ];
  }
  return [{ action: "move", items, parent: target.id || null }];
}

export function unassignedLights(data, filters = {}, hierarchy = true) {
  const assigned = new Set(
    Object.entries(data.leaves)
      .filter(([, leaf]) => leaf.parent_group_id !== null)
      .map(([id]) => data.routes[id]),
  );
  return filterLights(data.inventory, filters).filter(
    (light) => !hierarchy || !assigned.has(light.entity_id),
  );
}

export function groupStats(data) {
  const siblings = hierarchyIndex(data);
  const result = {},
    lights = new Map(
      data.inventory.lights.map((light) => [light.entity_id, light]),
    );
  const stack = siblings("group", null).map((id) => [id, false]);
  while (stack.length) {
    const [id, visited] = stack.pop(),
      children = siblings("group", id),
      leaves = siblings("leaf", id);
    if (!visited) {
      stack.push([id, true], ...children.map((child) => [child, false]));
      continue;
    }
    const stats = {
      groups: children.length,
      direct: leaves.length,
      total: leaves.length,
      unavailable: 0,
      on: 0,
      off: 0,
    };
    for (const leaf of leaves) {
      const state = lights.get(data.routes[leaf])?.state;
      if (state === "on") stats.on++;
      else if (state === "off") stats.off++;
      else stats.unavailable++;
    }
    for (const child of children) {
      for (const property of ["total", "unavailable", "on", "off"])
        stats[property] += result[child][property];
    }
    stats.state =
      stats.on && stats.off
        ? "Mixed"
        : stats.on
          ? "On"
          : stats.off
            ? "Off"
            : stats.total
              ? "Unavailable"
              : "Empty";
    result[id] = stats;
  }
  return result;
}

export function selectVisible(
  selection,
  keys,
  key,
  anchor,
  { toggle = false, range = false } = {},
) {
  const next = toggle ? new Set(selection) : new Set();
  if (range && keys.includes(anchor)) {
    const from = keys.indexOf(anchor),
      to = keys.indexOf(key);
    keys
      .slice(Math.min(from, to), Math.max(from, to) + 1)
      .forEach((item) => next.add(item));
  } else if (toggle && next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

export function movableItems(data, items) {
  const selected = new Set(
    items.filter((item) => item.kind === "group").map((item) => item.id),
  );
  return items.filter((item) => {
    const records = item.kind === "group" ? data.groups : data.leaves;
    let parent = records[item.id]?.parent_group_id;
    while (parent) {
      if (selected.has(parent)) return false;
      parent = data.groups[parent]?.parent_group_id;
    }
    return true;
  });
}

export function planDrop(data, rawItems, target, position = "inside") {
  const items = movableItems(data, rawItems);
  if (!items.length) throw Error("Select items to move.");
  const records = target.kind === "group" ? data.groups : data.leaves;
  const parent =
    position === "inside"
      ? target.id || null
      : records[target.id]?.parent_group_id;
  if (position === "inside" && target.kind !== "group")
    throw Error("Drop inside a group, or before/after a light.");
  if (parent && !data.groups[parent])
    throw Error("Destination no longer exists.");
  for (const item of items) {
    if (item.id === target.id) throw Error("Choose a different destination.");
    if (item.kind === "group") {
      let ancestor = parent;
      while (ancestor) {
        if (ancestor === item.id)
          throw Error("A group cannot move into its own subtree.");
        ancestor = data.groups[ancestor]?.parent_group_id;
      }
    }
  }
  if (items.some((item) => item.kind === "inventory")) {
    if (items.some((item) => item.kind !== "inventory") || !parent)
      throw Error("Drop unassigned lights inside a group.");
    const available = new Set(
      unassignedLights(data).map((light) => light.entity_id),
    );
    if (items.some((item) => !available.has(item.id)))
      throw Error("A selected light is already assigned. Refresh first.");
    if (position !== "inside")
      throw Error("Drop available lights inside a group.");
    return [
      {
        action: "add_lights",
        parent,
        entity_ids: items.map((item) => item.id),
      },
    ];
  }
  const operations = [];
  const moving = items.filter(
    (item) =>
      (item.kind === "group" ? data.groups : data.leaves)[item.id]
        ?.parent_group_id !== parent,
  );
  if (moving.length) operations.push({ action: "move", items: moving, parent });
  if (position !== "inside") {
    if (items.some((item) => item.kind !== target.kind))
      throw Error("Reorder groups with groups, and lights with lights.");
    const ids = siblingIds(data, target.kind, parent).filter(
      (id) => !items.some((item) => item.id === id),
    );
    const at = ids.indexOf(target.id);
    if (at < 0) throw Error("Destination no longer exists.");
    ids.splice(
      at + (position === "after" ? 1 : 0),
      0,
      ...items.map((item) => item.id),
    );
    operations.push({ action: "reorder", kind: target.kind, parent, ids });
  }
  return operations;
}
