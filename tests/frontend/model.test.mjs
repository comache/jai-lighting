import test from "node:test";
import assert from "node:assert/strict";
import {
  escapeHtml,
  treeRows,
  filterLights,
  dropOperations,
  reorderOperation,
} from "../../custom_components/jai_lighting/frontend/model.js";

const data = {
  groups: {
    root: { name: "Root", parent_group_id: null, sibling_order: 0 },
    child: { name: "Child", parent_group_id: "root", sibling_order: 0 },
    other: { name: "Other", parent_group_id: null, sibling_order: 1 },
  },
  leaves: {
    one: { parent_group_id: "child", sibling_order: 0 },
    two: { parent_group_id: "child", sibling_order: 1 },
  },
  routes: { one: "light.one", two: "light.two" },
  inventory: {
    lights: [
      {
        entity_id: "light.one",
        name: "Test One",
        labels: ["test"],
        area_id: "area",
        device_id: "device",
      },
      {
        entity_id: "light.two",
        name: "Test Two",
        labels: [],
        area_id: null,
        device_id: null,
      },
    ],
  },
};

test("expand/collapse and search retain ancestor paths", () => {
  assert.deepEqual(
    treeRows(data, new Set()).map((row) => row.id),
    ["root", "other"],
  );
  assert.deepEqual(
    treeRows(data, new Set(["root", "child"])).map((row) => row.id),
    ["root", "child", "one", "two", "other"],
  );
  assert.deepEqual(
    treeRows(data, new Set(), "test one").map((row) => row.id),
    ["root", "child", "one"],
  );
});
test("combined name/id/label/area/device filters are selection only", () => {
  assert.deepEqual(
    filterLights(data.inventory, {
      query: "LIGHT.ONE",
      label: "test",
      area_id: "area",
      device_id: "device",
    }).map((light) => light.entity_id),
    ["light.one"],
  );
  assert.equal(filterLights(data.inventory, { label: "missing" }).length, 0);
  assert.equal(data.leaves.one.parent_group_id, "child");
});
test("drop supports lights, groups, subgroups, root and sibling order", () => {
  for (const kind of ["leaf", "group"]) {
    const id = kind === "leaf" ? "one" : "child";
    assert.deepEqual(
      dropOperations(data, [{ kind, id }], { kind: "group", id: "other" }),
      [{ action: "move", items: [{ kind, id }], parent: "other" }],
    );
    assert.equal(
      dropOperations(data, [{ kind, id }], { kind: "group", id: "" })[0].parent,
      null,
    );
  }
  assert.deepEqual(
    dropOperations(
      data,
      [{ kind: "leaf", id: "two" }],
      { kind: "leaf", id: "one" },
      true,
    )[0].ids,
    ["two", "one"],
  );
  assert.deepEqual(reorderOperation(data, "leaf", "two", -1).ids, [
    "two",
    "one",
  ]);
  assert.equal(reorderOperation(data, "leaf", "one", -1), null);
});
test("all user labels are escaped before HTML insertion", () => {
  assert.equal(
    escapeHtml('<img src="x" onerror="bad"> &'),
    "&lt;img src=&quot;x&quot; onerror=&quot;bad&quot;&gt; &amp;",
  );
});

import {
  groupStats,
  unassignedLights,
  planDrop,
  movableItems,
  selectVisible,
} from "../../custom_components/jai_lighting/frontend/model.js";

test("unassigned membership is explicit and label filtering cannot leak assigned lights", () => {
  const current = structuredClone(data);
  current.leaves.two.parent_group_id = null;
  assert.deepEqual(
    unassignedLights(current).map((x) => x.entity_id),
    ["light.two"],
  );
  assert.equal(unassignedLights(current, { label: "test" }).length, 0);
  assert.equal(unassignedLights(current, { label: "test" }, false).length, 1);
});
test("actual counts aggregate available, OFF and missing descendants independently of expansion", () => {
  const current = structuredClone(data);
  current.inventory.lights[0].state = "on";
  current.inventory.lights[1].state = "off";
  let stats = groupStats(current);
  assert.equal(stats.root.groups, 1);
  assert.equal(stats.root.direct, 0);
  assert.equal(stats.root.total, 2);
  assert.equal(stats.child.state, "Mixed");
  assert.equal(stats.other.state, "Empty");
  current.inventory.lights.pop();
  stats = groupStats(current);
  assert.equal(stats.root.unavailable, 1);
  assert.equal(stats.root.on, 1);
  assert.equal(stats.child.total, 2);
});
test("range and modifier selection use stable visible keys and a missing anchor is safe", () => {
  const keys = [
    "group:root",
    "group:child",
    "leaf:one",
    "leaf:two",
    "group:other",
  ];
  assert.deepEqual(
    [
      ...selectVisible(new Set(["group:root"]), keys, "group:other", null, {
        toggle: true,
      }),
    ],
    ["group:root", "group:other"],
  );
  assert.deepEqual(
    [
      ...selectVisible(new Set(), keys, "leaf:two", "group:child", {
        range: true,
      }),
    ],
    ["group:child", "leaf:one", "leaf:two"],
  );
  assert.deepEqual(
    [...selectVisible(new Set(), keys, "leaf:one", "deleted", { range: true })],
    ["leaf:one"],
  );
});
test("drag planner preserves whole subtrees, rejects cycles and reorders multiple siblings before/after", () => {
  const current = structuredClone(data);
  const selected = [
    { kind: "group", id: "root" },
    { kind: "group", id: "child" },
    { kind: "leaf", id: "one" },
  ];
  assert.deepEqual(movableItems(current, selected), [
    { kind: "group", id: "root" },
  ]);
  assert.throws(
    () => planDrop(current, selected, { kind: "group", id: "child" }),
    /subtree/,
  );
  assert.throws(
    () =>
      planDrop(current, [{ kind: "group", id: "root" }], {
        kind: "group",
        id: "root",
      }),
    /different/,
  );
  assert.deepEqual(
    planDrop(
      current,
      [{ kind: "leaf", id: "two" }],
      { kind: "leaf", id: "one" },
      "before",
    )[0].ids,
    ["two", "one"],
  );
  assert.deepEqual(
    planDrop(
      current,
      [{ kind: "leaf", id: "one" }],
      { kind: "leaf", id: "two" },
      "after",
    )[0].ids,
    ["two", "one"],
  );
  assert.equal(
    planDrop(current, [{ kind: "group", id: "child" }], {
      kind: "group",
      id: "",
    })[0].parent,
    null,
  );
  assert.throws(
    () =>
      planDrop(current, [{ kind: "inventory", id: "light.one" }], {
        kind: "group",
        id: "other",
      }),
    /already assigned/,
  );
});
