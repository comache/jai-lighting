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
