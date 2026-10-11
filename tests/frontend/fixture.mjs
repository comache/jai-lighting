// Synthetic configuration only. The Python suite exercises the real HA transport/store.
export const sample = {
  schema_version: 2,
  config_revision: 0,
  state_revision: 0,
  recovery_pending: false,
  runtime: {
    group_masters: { root: 255, child: 255, other: 255 },
    leaf_brightness_targets: {},
  },
  groups: {
    root: {
      name: "Root",
      parent_group_id: null,
      sibling_order: 0,
      power_policy: "RESTORE",
    },
    child: {
      name: "Child",
      parent_group_id: "root",
      sibling_order: 0,
      power_policy: "RESTORE",
    },
    other: {
      name: "Other",
      parent_group_id: null,
      sibling_order: 1,
      power_policy: "RESTORE",
    },
  },
  leaves: {
    one: { parent_group_id: "child", sibling_order: 0 },
    two: { parent_group_id: "other", sibling_order: 0 },
  },
  routes: { one: "light.one", two: "light.two" },
  entities: {
    root: "light.jai_root",
    child: "light.jai_child",
    other: "light.jai_other",
  },
  collections: {},
  presets: {},
  inventory: {
    lights: [
      {
        entity_id: "light.one",
        name: "Test One",
        state: "off",
        labels: ["test"],
        area_id: "area",
        device_id: "device",
      },
      {
        entity_id: "light.two",
        name: "Test Two",
        state: "off",
        labels: [],
        area_id: null,
        device_id: null,
      },
      {
        entity_id: "light.three",
        name: "Available Three",
        state: "unavailable",
        labels: ["test"],
        area_id: "area",
        device_id: "device",
      },
      {
        entity_id: "light.four",
        name: "Available Four",
        state: "off",
        labels: [],
        area_id: null,
        device_id: null,
      },
    ],
    areas: [{ id: "area", name: "Test Area" }],
    labels: [{ id: "test", name: "Test Label" }],
    devices: [{ id: "device", name: "Test Device" }],
  },
};
export function fixtureApply(before, operations) {
  const state = structuredClone(before);
  const order = (kind, parent) =>
    Math.max(
      -1,
      ...Object.values(kind === "group" ? state.groups : state.leaves)
        .filter((row) => row.parent_group_id === parent)
        .map((row) => row.sibling_order),
    ) + 1;
  const register = (entity, parent = null) => {
    let id = Object.keys(state.routes).find(
      (id) => state.routes[id] === entity,
    );
    if (!id) {
      id = `registered-${entity}`;
      state.routes[id] = entity;
      state.leaves[id] = {
        parent_group_id: null,
        sibling_order: order("leaf", null),
      };
    }
    if (parent)
      state.leaves[id] = {
        ...state.leaves[id],
        parent_group_id: parent,
        sibling_order: order("leaf", parent),
      };
    return id;
  };
  for (const op of operations) {
    if (op.action === "move")
      for (const item of op.items) {
        const records = item.kind === "group" ? state.groups : state.leaves;
        records[item.id] = {
          ...records[item.id],
          parent_group_id: op.parent,
          sibling_order: order(item.kind, op.parent),
        };
      }
    else if (op.action === "reorder")
      op.ids.forEach(
        (id, index) =>
          ((op.kind === "group" ? state.groups : state.leaves)[
            id
          ].sibling_order = index),
      );
    else if (op.action === "restore_positions")
      op.positions.forEach((row) =>
        Object.assign(
          (row.kind === "group" ? state.groups : state.leaves)[row.id],
          { parent_group_id: row.parent, sibling_order: row.order },
        ),
      );
    else if (op.action === "add_lights")
      op.entity_ids.forEach((entity) => register(entity, op.parent));
    else if (op.action === "remove_lights")
      op.ids.forEach((id) =>
        Object.assign(state.leaves[id], {
          parent_group_id: null,
          sibling_order: order("leaf", null),
        }),
      );
    else if (op.action === "update_group") {
      for (const [key, value] of Object.entries(op))
        if (["name", "parent", "order", "policy"].includes(key))
          state.groups[op.id][
            {
              parent: "parent_group_id",
              order: "sibling_order",
              policy: "power_policy",
            }[key] || key
          ] = value;
    } else if (op.action === "update_collection") {
      const id = op.id || "new-collection",
        old = state.collections[id];
      const members = op.members || old?.members || [];
      state.collections[id] = {
        name: op.name || old?.name,
        members: [
          ...new Set([
            ...members,
            ...(op.add_entity_ids || []).map((entity) => register(entity)),
          ]),
        ],
      };
    } else if (op.action === "update_preset")
      state.presets[op.id] = {
        name: op.name,
        targets: op.targets.map((row) => ({
          target: { kind: row.kind, id: row.id },
          properties: row.properties,
        })),
      };
    else if (op.action === "delete_group") {
      delete state.groups[op.id];
      delete state.entities[op.id];
    } else if (op.action === "delete_collection")
      delete state.collections[op.id];
    else if (op.action === "delete_preset") delete state.presets[op.id];
    else throw Error("Unsupported fixture operation");
  }
  state.config_revision++;
  const positions = [];
  for (const [kind, old, records] of [
    ["group", before.groups, state.groups],
    ["leaf", before.leaves, state.leaves],
  ]) {
    for (const [id, row] of Object.entries(records)) {
      const previous = old[id];
      if (
        !previous ||
        previous.parent_group_id !== row.parent_group_id ||
        previous.sibling_order !== row.sibling_order
      )
        positions.push({
          kind,
          id,
          parent: previous?.parent_group_id ?? null,
          order: previous?.sibling_order ?? row.sibling_order,
        });
    }
  }
  return {
    state,
    undo_operations: positions.length
      ? [{ action: "restore_positions", positions }]
      : [],
  };
}
export function fixtureHtml({ data = sample, theme = "light" } = {}) {
  const colors =
    theme === "dark"
      ? "--primary-background-color:#15191e;--card-background-color:#20262e;--secondary-background-color:#2b333e;--primary-text-color:#ebeff5;--secondary-text-color:#9aa8b9;--divider-color:#343d48;--primary-color:#69bfea"
      : "--primary-background-color:#f5f7fa;--card-background-color:#fff;--primary-text-color:#20252d;--primary-color:#007aab";
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>J’ai Lighting — Development preview</title><style>body{margin:0;${colors}}</style></head><body><script type="module">
  import "/manager.js";
  window.requests=[];window.sample=${JSON.stringify(data)};
  const apply=${fixtureApply.toString()};
  const panel=document.createElement("jai-lighting-manager");window.panel=panel;
  panel.hass={user:{id:"fixture-user"},connection:{subscribeEvents:async(callback,type)=>{window.managerEvent=callback;window.eventType=type;return()=>{window.managerEvent=null;};}},callWS:async message=>{
    requests.push(structuredClone(message));
    if(message.command==="get") return structuredClone(sample);
    if(["preview","apply"].includes(message.command)) {
      if(message.revision !== sample.config_revision) throw Error("Configuration changed; refresh before editing");
      if(message.command === "apply") {
        if(window.saveDelay) await new Promise(resolve=>setTimeout(resolve,window.saveDelay));
        if(window.failNextApply) {window.failNextApply=false;throw Error("Disk failure: change not saved");}
        if(window.concurrentBeforeApply) {window.concurrentBeforeApply=false;sample.config_revision++;sample.groups.other.name="Other administrator";throw Error("Configuration changed; refresh before editing");}
      }
      const result=apply(sample,message.operations);
      if(message.command==="preview")return {snapshot:result.state,presets_before:{},presets_after:{}};
      window.sample=result.state;return {revision:sample.config_revision,undo_operations:result.undo_operations};
    }
    if(message.command==="create") {window.draft=message;return {flow_id:"private-draft",preview:"No preset effective values change."};}
    if(message.command==="save_creation") {
      sample.config_revision++;sample.groups["new-entry"]={name:draft.data.name,parent_group_id:draft.data.parent || null,power_policy:draft.data.power_policy,sibling_order:0};sample.entities["new-entry"]="light.new_group";return {entry_id:"new-entry"};
    }
    if(message.command==="cancel_creation")return {};
    if(message.command==="control")return {status:"success",succeeded:2,skipped:{},failed:{}};
    throw Error("Unknown test command");
  },callService:async(domain,service,data)=>requests.push({domain,service,data})};
  document.body.append(panel);
  </script></body></html>`;
}

export function largeSample() {
  const data = structuredClone(sample);
  data.groups = {};
  data.leaves = {};
  data.routes = {};
  data.entities = {};
  data.runtime.group_masters = {};
  data.inventory.lights = [];
  data.collections = {};
  for (let i = 0; i < 200; i++) {
    const id = `g-${i}`;
    data.groups[id] = {
      name: `Group ${String(i).padStart(3, "0")}`,
      parent_group_id: i > 0 && i < 20 ? `g-${i - 1}` : null,
      sibling_order: i,
      power_policy: "RESTORE",
    };
    data.entities[id] = `light.virtual_${i}`;
    data.runtime.group_masters[id] = 255;
  }
  for (let i = 0; i < 300; i++) {
    const entity_id = `light.synthetic_${i}`;
    data.inventory.lights.push({
      entity_id,
      name: `Synthetic ${String(i).padStart(3, "0")}`,
      state: i % 10 === 0 ? "unavailable" : "off",
      labels: ["test"],
      area_id: "area",
      device_id: "device",
    });
    if (i < 150) {
      data.leaves[`l-${i}`] = { parent_group_id: `g-${i}`, sibling_order: 0 };
      data.routes[`l-${i}`] = entity_id;
    }
  }
  for (let i = 0; i < 60; i++)
    data.collections[`c-${i}`] = {
      name: `Collection ${i}`,
      members: [`l-${i}`],
    };
  return data;
}
