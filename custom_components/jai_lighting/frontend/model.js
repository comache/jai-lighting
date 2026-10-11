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

export function treeRows(data, expanded, query = "") {
  const rows = [],
    stack = siblingIds(data, "group", null)
      .reverse()
      .map((id) => ({ kind: "group", id, depth: 0 }));
  const needle = query.trim().toLocaleLowerCase();
  // Filtering keeps matching descendants and their ancestors visible.
  const matches = new Set();
  if (needle) {
    for (const [id, leaf] of Object.entries(data.leaves)) {
      const light = data.inventory.lights.find(
        (l) => l.entity_id === data.routes[id],
      );
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
      const children = siblingIds(data, "group", row.id).map((id) => ({
        kind: "group",
        id,
        depth: row.depth + 1,
      }));
      const leaves = siblingIds(data, "leaf", row.id).map((id) => ({
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
