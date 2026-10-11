import test from "node:test";
import assert from "node:assert/strict";
import {
  commandFor,
  resolve,
  childrenOf,
  reorder,
  refOf,
  keyOf,
} from "../../custom_components/jai_lighting/frontend/control-model.js";
import { catalog, states, group, entity } from "./control-fixture.mjs";
test("stable group identities resolve renamed entity IDs without paths", () => {
  const c = structuredClone(catalog);
  c.groups.lounge.entity_id = "light.renamed";
  const s = { ...states, "light.renamed": states["light.synthetic_lounge"] };
  assert.equal(
    commandFor(group("lounge"), "brightness", 35, c, s).data.entity_id,
    "light.renamed",
  );
});
test("OFF-safe masters use set_master, including generic references to virtual entities", () => {
  for (const ref of [group("lounge"), entity("light.synthetic_lounge")]) {
    const p = commandFor(ref, "brightness", 23, catalog, states);
    assert.equal(p.service, "set_master");
    assert.equal(p.domain, "jai_lighting");
    assert.equal(states["light.synthetic_lounge"].state, "off");
  }
  assert.throws(() =>
    commandFor(group("lounge"), "brightness", 0, catalog, states),
  );
});
test("power and physical brightness follow separate supported native routes", () => {
  assert.equal(
    commandFor(group("lounge"), "power", null, catalog, states).service,
    "turn_on",
  );
  assert.equal(
    commandFor(
      entity("light.synthetic_glow"),
      "brightness",
      50,
      catalog,
      states,
    ).domain,
    "light",
  );
  assert.throws(() =>
    commandFor(
      entity("light.synthetic_spot"),
      "brightness",
      50,
      catalog,
      states,
    ),
  );
  assert.equal(
    commandFor(entity("switch.synthetic_plug"), "power", null, catalog, states)
      .domain,
    "switch",
  );
});
test("collections expose absolute controls and no relative masters", () => {
  const ref = { kind: "collection", id: "upper" };
  assert(resolve(ref, catalog, states).absolute);
  assert(resolve(ref, catalog, states).mixed);
  const p = commandFor(ref, "brightness", 30, catalog, states);
  assert.equal(p.command, "collection");
  assert.deepEqual(p.properties, { brightness_pct: 30 });
  assert.equal(p.service, undefined);
});
test("unactivated unknown scene remains explicitly activatable", () => {
  assert.equal(
    commandFor({ kind: "preset", id: "evening" }, "mood", null, catalog, states)
      .service,
    "activate_preset",
  );
});
test("recursive children are immediate and do not flatten descendants", () => {
  assert.deepEqual(childrenOf("main", catalog), [
    group("lounge"),
    group("stage"),
  ]);
  assert.deepEqual(childrenOf("lounge", catalog), [
    group("accent"),
    entity("light.synthetic_glow"),
  ]);
  assert.deepEqual(childrenOf("missing", catalog), []);
});
test("unavailable media and unsupported features cannot dispatch", () => {
  const s = structuredClone(states);
  s["media_player.synthetic_music"].state = "unavailable";
  assert.throws(() =>
    commandFor(
      entity("media_player.synthetic_music"),
      "play",
      null,
      catalog,
      s,
    ),
  );
  s["media_player.synthetic_music"].state = "paused";
  s["media_player.synthetic_music"].attributes.supported_features = 0;
  assert.throws(() =>
    commandFor(
      entity("media_player.synthetic_music"),
      "play",
      null,
      catalog,
      s,
    ),
  );
});
test("bounded media volume, reordering and reference round-trip", () => {
  assert.equal(
    commandFor(
      entity("media_player.synthetic_music"),
      "volume",
      140,
      catalog,
      states,
    ).data.volume_level,
    1,
  );
  assert.deepEqual(reorder([{ id: "a" }, { id: "b" }], "b", "a"), [
    { id: "b" },
    { id: "a" },
  ]);
  assert.deepEqual(
    refOf(keyOf(group("nested:stable"))),
    group("nested:stable"),
  );
});
