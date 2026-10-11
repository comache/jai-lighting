// Local synthetic dashboard, with no production host, token or external APIs.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { controlFixture } from "../tests/frontend/control-fixture.mjs";
const modules = new Set([
  "control.js",
  "control-model.js",
  "control-styles.js",
  "model.js",
  "dom.js",
]);
export function previewServer() {
  return createServer(async (req, res) => {
    const path = new URL(req.url, "http://127.0.0.1").pathname;
    if (path === "/") {
      res.setHeader("Content-Type", "text/html");
      res.end(controlFixture());
    } else if (modules.has(path.slice(1))) {
      res.setHeader("Content-Type", "text/javascript");
      res.end(
        await readFile(
          new URL(
            "../custom_components/jai_lighting/frontend" + path,
            import.meta.url,
          ),
        ),
      );
    } else {
      res.writeHead(404);
      res.end();
    }
  });
}
if (
  process.argv[1] &&
  import.meta.url === new URL("file://" + process.argv[1]).href
) {
  const server = previewServer();
  server.listen(0, "127.0.0.1", () =>
    console.log(
      "Synthetic J’ai Control preview: http://127.0.0.1:" +
        server.address().port,
    ),
  );
}
