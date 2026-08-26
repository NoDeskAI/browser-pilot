import { createServer } from "node:http";
import { platform } from "node:os";

const DEFAULT_TIMEOUTS = Object.freeze({
  script: 30_000,
  pageLoad: 60_000,
  implicit: 0,
});

class WebDriverError extends Error {
  constructor(errorName, message, status = 500) {
    super(message);
    this.name = "WebDriverError";
    this.errorName = errorName;
    this.status = status;
  }
}

function asInteger(value, fallback, name, minimum = 0, maximum = 86_400_000) {
  if (value === undefined) return fallback;
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new WebDriverError("invalid argument", `${name} must be an integer between ${minimum} and ${maximum}`, 400);
  }
  return parsed;
}

async function readJsonBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 5 * 1024 * 1024) {
      throw new WebDriverError("invalid argument", "Request body exceeds 5 MiB", 413);
    }
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new WebDriverError("invalid argument", "Request body must be valid JSON", 400);
  }
}

function sendJson(response, status, value) {
  const body = JSON.stringify({ value });
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

function sendError(response, error) {
  const status = error instanceof WebDriverError ? error.status : 500;
  const errorName = error instanceof WebDriverError ? error.errorName : "unknown error";
  sendJson(response, status, {
    error: errorName,
    message: error?.message ?? String(error),
    stacktrace: "",
  });
}

export function createBrowserLiteServer(state) {
  return createServer(async (request, response) => {
    try {
      const method = request.method ?? "GET";
      const url = new URL(request.url ?? "/", "http://browser-lite.local");
      const path = decodeURIComponent(url.pathname).replace(/\/$/, "") || "/";

      if (method === "GET" && (path === "/healthz" || path === "/browser-lite/status")) {
        sendJson(response, 200, await state.runtimeStatus());
        return;
      }
      if (method === "GET" && path === "/status") {
        await state.ensureConnected();
        sendJson(response, 200, {
          ready: true,
          message: "Browser Lite embedded runtime ready",
          nodes: [{ slots: [{ session: { sessionId: state.sessionId } }] }],
        });
        return;
      }
      if (method === "POST" && path === "/session") {
        await readJsonBody(request);
        await state.ensureConnected();
        sendJson(response, 200, {
          sessionId: state.sessionId,
          capabilities: {
            browserName: "chrome",
            browserVersion: state.browserVersion ?? "browser_lite",
            platformName: platform(),
            acceptInsecureCerts: false,
            setWindowRect: true,
            "browser-lite:instanceId": state.config.instanceId,
            "browser-lite:embedded": true,
          },
        });
        return;
      }

      const match = path.match(/^\/session\/([^/]+)(\/.*)?$/);
      if (!match) throw new WebDriverError("unknown command", `${method} ${path} is not implemented`, 404);
      const command = match[2] || "";
      if (match[1] !== state.sessionId) {
        throw new WebDriverError("invalid session id", `Unknown session: ${match[1]}`, 404);
      }
      await state.ensureConnected();

      if (method === "DELETE" && command === "") sendJson(response, 200, null);
      else if (method === "GET" && command === "/url") sendJson(response, 200, await state.currentUrl());
      else if (method === "POST" && command === "/url") {
        const body = await readJsonBody(request);
        await state.navigate(body.url);
        sendJson(response, 200, null);
      } else if (method === "POST" && command === "/refresh") {
        await state.sendPage("Page.reload", {});
        await state.waitForDocumentReady();
        sendJson(response, 200, null);
      } else if (method === "POST" && (command === "/back" || command === "/forward")) {
        const history = await state.sendPage("Page.getNavigationHistory");
        const offset = command === "/back" ? -1 : 1;
        const entry = history.entries?.[history.currentIndex + offset];
        if (entry) {
          await state.sendPage("Page.navigateToHistoryEntry", { entryId: entry.id });
          await state.waitForDocumentReady();
        }
        sendJson(response, 200, null);
      } else if (method === "GET" && command === "/title") sendJson(response, 200, await state.title());
      else if (method === "GET" && command === "/source") sendJson(response, 200, await state.source());
      else if (method === "GET" && command === "/timeouts") sendJson(response, 200, { ...state.timeouts });
      else if (method === "POST" && command === "/timeouts") {
        const body = await readJsonBody(request);
        for (const key of Object.keys(DEFAULT_TIMEOUTS)) {
          if (body[key] !== undefined && body[key] !== null) {
            state.timeouts[key] = asInteger(body[key], state.timeouts[key], key);
          }
        }
        sendJson(response, 200, null);
      } else if (method === "GET" && command === "/cookie") sendJson(response, 200, await state.cookies());
      else if (method === "POST" && command === "/cookie") {
        const body = await readJsonBody(request);
        await state.addCookie(body.cookie);
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command === "/cookie") {
        await state.deleteCookie();
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command.startsWith("/cookie/")) {
        await state.deleteCookie(command.slice("/cookie/".length));
        sendJson(response, 200, null);
      } else if (method === "POST" && command === "/execute/sync") {
        const body = await readJsonBody(request);
        sendJson(response, 200, await state.execute(body.script, body.args));
      } else if (method === "GET" && command === "/screenshot") sendJson(response, 200, await state.screenshot());
      else if (method === "POST" && command === "/actions") {
        const body = await readJsonBody(request);
        await state.performActions(body.actions);
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command === "/actions") sendJson(response, 200, null);
      else if (method === "POST" && command === "/goog/cdp/execute") {
        const body = await readJsonBody(request);
        if (!body.cmd) throw new WebDriverError("invalid argument", "Missing CDP cmd", 400);
        sendJson(response, 200, await state.sendPage(body.cmd, body.params ?? {}));
      } else if (method === "GET" && command === "/window/handles") {
        sendJson(response, 200, (await state.targets()).map((target) => target.targetId));
      } else if (method === "GET" && command === "/window") sendJson(response, 200, await state.ensurePage());
      else if (method === "POST" && command === "/window") {
        const body = await readJsonBody(request);
        await state.setActiveTarget(body.handle);
        sendJson(response, 200, null);
      } else if (method === "DELETE" && command === "/window") sendJson(response, 200, await state.closeActiveTarget());
      else if (method === "GET" && command === "/window/rect") sendJson(response, 200, await state.windowRect());
      else if (method === "POST" && command === "/window/rect") {
        sendJson(response, 200, await state.setWindowRect(await readJsonBody(request)));
      } else {
        throw new WebDriverError("unknown command", `${method} ${path} is not implemented`, 404);
      }
    } catch (error) {
      console.error("Browser Lite embedded WebDriver request failed", {
        method: request.method,
        url: request.url,
        error: error?.message ?? String(error),
      });
      sendError(response, error);
    }
  });
}
