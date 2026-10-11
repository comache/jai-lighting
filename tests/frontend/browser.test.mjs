// Isolated UI contract tests. Python tests exercise the real HA API separately.
import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const { chromium } = await import(
  process.env.JAI_PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.JAI_PLAYWRIGHT_MODULE).href
    : "playwright"
);
let browser, server, url;
const sample = {
  schema_version: 2,
  config_revision: 0,
  state_revision: 0,
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
    ],
    areas: [{ id: "area", name: "Test Area" }],
    labels: [{ id: "test", name: "Test Label" }],
    devices: [{ id: "device", name: "Test Device" }],
  },
};

before(async () => {
  server = createServer(async (req, res) => {
    if (req.url === "/") {
      res.setHeader("Content-Type", "text/html");
      res.end(`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}</style><script type="module">
        import "/manager.js";
        window.requests=[];window.sample=${JSON.stringify(sample)};
        const panel=document.createElement("jai-lighting-manager");
        panel.hass={
          callWS:async message=>{
            requests.push(message);
            if(message.command==="get")return structuredClone(sample);
            if(message.command==="preview")return {snapshot:sample,presets_before:{},presets_after:{}};
            if(message.command==="apply"){sample.config_revision++;return {revision:sample.config_revision};}
            if(message.command==="create")return {flow_id:"private-draft",preview:"No preset effective values change."};
            if(message.command==="save_creation")return {entry_id:"new-entry"};
            if(message.command==="cancel_creation")return {};
            if(message.command==="control")return {status:"success",succeeded:2,skipped:{},failed:{}};
            throw Error("Unknown test command");
          },callService:async(domain,service,data)=>requests.push({domain,service,data})
        };window.panel=panel;document.body.append(panel);
      </script>`);
      return;
    }
    if (!["/manager.js", "/model.js"].includes(req.url)) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.setHeader("Content-Type", "text/javascript");
    res.end(
      await readFile(
        new URL(
          `../../custom_components/jai_lighting/frontend${req.url}`,
          import.meta.url,
        ),
      ),
    );
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  url = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({
    headless: true,
    ...(process.env.JAI_BROWSER_CHANNEL
      ? { channel: process.env.JAI_BROWSER_CHANNEL }
      : {}),
  });
});
after(async () => {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
});

async function pageFor(width = 1440, touch = false) {
  const context = await browser.newContext({
    viewport: { width, height: 900 },
    hasTouch: touch,
  });
  const page = await context.newPage();
  const errors = [];
  page.setDefaultTimeout(5000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  await page.getByRole("tree", { name: "Lighting hierarchy" }).waitFor();
  if (process.env.JAI_SCREENSHOT_DIR) {
    await mkdir(process.env.JAI_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({
      path: `${process.env.JAI_SCREENSHOT_DIR}/manager-${width}.png`,
      fullPage: true,
    });
  }
  return { page, context, errors };
}
async function requestLog(page) {
  return page.evaluate(() => window.requests);
}

test("first group dialog reaches review and saves without lighting calls", async () => {
  const { page, context, errors } = await pageFor();
  await page.getByRole("button", { name: "Create group", exact: true }).click();
  await page.getByRole("textbox", { name: "Group name" }).fill("New Root");
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("heading", { name: "Review new group" }).waitFor();
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached" });
  const log = await requestLog(page),
    create = log.find((message) => message.command === "create");
  assert.equal(create.data.parent, "");
  assert.deepEqual(create.data.children, []);
  assert.equal(create.data.allow_moves, false);
  assert.equal(create.data.order, 0);
  assert(log.some((message) => message.command === "save_creation"));
  assert(!log.some((message) => message.domain === "light"));
  assert.deepEqual(errors, []);
  await context.close();
});
test("label filtering, bulk selection and editable explicit collections", async () => {
  const { page, context, errors } = await pageFor();
  await page.getByLabel("HA label", { exact: true }).selectOption("test");
  assert.equal(await page.locator("[data-light]").count(), 1);
  await page.getByRole("button", { name: "Select all matching" }).click();
  await page.getByRole("button", { name: "Collections", exact: true }).click();
  await page
    .getByRole("button", { name: "Create collection", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Test Collection");
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("heading", { name: "Review changes" }).waitFor();
  const log = await requestLog(page),
    preview = log.find((message) => message.command === "preview");
  assert.deepEqual(preview.operations[0].add_entity_ids, ["light.one"]);
  assert(!("filters" in preview.operations[0]));
  assert.deepEqual(errors, []);
  await context.close();
});
test("desktop drag and keyboard move alternatives send equivalent operations", async () => {
  const { page, context, errors } = await pageFor();
  await page
    .locator('[data-drag="leaf:one"]')
    .dragTo(page.locator('[data-drop="group:other"]'));
  await page.getByRole("heading", { name: "Review changes" }).waitFor();
  const drag = (await requestLog(page)).find(
    (message) => message.command === "preview",
  ).operations[0];
  assert.equal(drag.action, "move");
  assert.equal(drag.parent, "other");
  assert.deepEqual(drag.items, [{ kind: "leaf", id: "one" }]);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.locator('[data-select="leaf:one"]').check();
  await page.getByRole("button", { name: "Move to…" }).click();
  await page.getByLabel("Destination", { exact: true }).selectOption("other");
  await page.getByRole("button", { name: "Continue / save" }).click();
  const moves = (await requestLog(page)).filter(
    (message) => message.command === "preview",
  );
  assert.deepEqual(moves.at(-1).operations[0], drag);
  assert.deepEqual(errors, []);
  await context.close();
});
for (const width of [390, 820])
  test(`responsive ${width}px has usable move and OFF-safe relative fader`, async () => {
    const { page, context, errors } = await pageFor(width, true);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      ),
      false,
    );
    await page.locator('[data-select="group:child"]').check();
    await page.getByRole("button", { name: "Move to…" }).click();
    await page.getByLabel("Destination", { exact: true }).selectOption("");
    await page.getByRole("button", { name: "Continue / save" }).click();
    await page.getByRole("heading", { name: "Review changes" }).waitFor();
    assert.equal(
      (await requestLog(page)).find((message) => message.command === "preview")
        .operations[0].parent,
      null,
    );
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page
      .getByRole("slider", { name: "Root relative master", exact: true })
      .fill("50");
    await page
      .getByRole("slider", { name: "Root relative master", exact: true })
      .dispatchEvent("change");
    const fader = (await requestLog(page)).find(
      (message) => message.service === "set_master",
    );
    assert(fader);
    assert.equal(fader.domain, "jai_lighting");
    assert(
      !(await requestLog(page)).some((message) => message.domain === "light"),
    );
    assert.deepEqual(errors, []);
    await context.close();
  });
test("preset graphical editor creates sparse collection targets", async () => {
  const { page, context, errors } = await pageFor();
  await page.evaluate(() => {
    window.sample.collections = {
      c: { name: "Test Collection", members: ["one"] },
    };
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("button", { name: "Presets", exact: true }).click();
  await page
    .getByRole("button", { name: "Create preset", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Name", exact: true })
    .fill("Test Recipe");
  await page.getByRole("button", { name: "Add assignment" }).click();
  await page.getByLabel("Target", { exact: true }).selectOption("collection:c");
  await page.getByLabel("Assign brightness (%)", { exact: true }).check();
  await page
    .getByRole("spinbutton", { name: "Brightness (%)", exact: true })
    .fill("35");
  await page.getByRole("button", { name: "Continue / save" }).click();
  await page.getByRole("heading", { name: "Review new preset" }).waitFor();
  const create = (await requestLog(page)).find(
    (message) => message.command === "create",
  );
  assert.deepEqual(create.data.targets, [
    {
      kind: "collection",
      id: "c",
      properties: { power: "on", brightness_pct: 35 },
    },
  ]);
  assert.deepEqual(errors, []);
  await context.close();
});

test("touch drag moves a light on phone without sending hardware commands", async () => {
  const { page, context, errors } = await pageFor(390, true);
  const handle = page.locator('[data-drag="leaf:one"]');
  const destination = page.locator('[data-drop="group:other"]');
  await destination.scrollIntoViewIfNeeded();
  const start = await handle.boundingBox(),
    end = await destination.boundingBox();
  const session = await context.newCDPSession(page);
  const point = (x, y) => [{ x, y, id: 1 }];
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: point(start.x + 12, start.y + 12),
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: point(end.x + 100, end.y + 25),
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await page.getByRole("heading", { name: "Review changes" }).waitFor();
  const preview = (await requestLog(page)).find(
    (message) => message.command === "preview",
  );
  assert.equal(preview.operations[0].parent, "other");
  assert(
    !(await requestLog(page)).some((message) => message.domain === "light"),
  );
  assert.deepEqual(errors, []);
  await context.close();
});
