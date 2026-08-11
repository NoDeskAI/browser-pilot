#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function xml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function run(command, args, { allowFailure = false } = {}) {
  const result = spawnSync(command, args, { encoding: "utf8", stdio: allowFailure ? "ignore" : "inherit" });
  if (!allowFailure && result.status !== 0) process.exit(result.status ?? 1);
}

if (process.platform !== "darwin") {
  process.stderr.write("install-launch-agent.mjs only supports macOS\n");
  process.exit(1);
}

const instanceId = option("--instance-id", "browser_lite");
if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(instanceId)) {
  process.stderr.write("Invalid --instance-id\n");
  process.exit(1);
}
const port = Number.parseInt(option("--port", "4444"), 10);
if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  process.stderr.write("Invalid --port\n");
  process.exit(1);
}
const host = option("--host", "127.0.0.1");
const label = `app.browser-pilot.browser-lite.${instanceId}`;
const uid = process.getuid();
const domain = `gui/${uid}`;
const launchAgentsDir = join(homedir(), "Library", "LaunchAgents");
const logDir = join(homedir(), "Library", "Logs", "Browser Pilot");
const plistPath = join(launchAgentsDir, `${label}.plist`);
const runtimePath = resolve(join(SCRIPT_DIR, "browser-lite.mjs"));
const logPath = join(logDir, `browser-lite-${instanceId}.log`);
const uninstall = process.argv.includes("--uninstall");
const dryRun = process.argv.includes("--dry-run");

if (uninstall) {
  run("launchctl", ["bootout", domain, plistPath], { allowFailure: true });
  rmSync(plistPath, { force: true });
  process.stdout.write(`Removed ${label}\n`);
  process.exit(0);
}

mkdirSync(launchAgentsDir, { recursive: true, mode: 0o700 });
mkdirSync(logDir, { recursive: true, mode: 0o700 });

const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${xml(label)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${xml(process.execPath)}</string>
    <string>${xml(runtimePath)}</string>
    <string>--host</string><string>${xml(host)}</string>
    <string>--port</string><string>${port}</string>
    <string>--instance-id</string><string>${xml(instanceId)}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${xml(SCRIPT_DIR)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>StandardOutPath</key>
  <string>${xml(logPath)}</string>
  <key>StandardErrorPath</key>
  <string>${xml(logPath)}</string>
</dict>
</plist>
`;

if (dryRun) {
  process.stdout.write(plist);
  process.exit(0);
}

writeFileSync(plistPath, plist, { mode: 0o600 });
run("plutil", ["-lint", plistPath]);
run("launchctl", ["bootout", domain, plistPath], { allowFailure: true });
run("launchctl", ["bootstrap", domain, plistPath]);
run("launchctl", ["enable", `${domain}/${label}`]);
run("launchctl", ["kickstart", "-k", `${domain}/${label}`]);
process.stdout.write(`${JSON.stringify({ label, plistPath, logPath, host, port, instanceId }, null, 2)}\n`);
