#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { copyFile, cp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import packager from "@electron/packager";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(ROOT, "dist");
const TEMP = join(ROOT, "build", "dmg-staging");
const APP_NAME = "Browser Lite";
const APP_PATH = join(DIST, `${APP_NAME}-darwin-arm64`, `${APP_NAME}.app`);
const packageManifest = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
const DMG_PATH = join(DIST, `Browser-Lite-${packageManifest.version}-arm64.dmg`);

function run(command, args, options = {}) {
  execFileSync(command, args, { stdio: "inherit", ...options });
}

function signingIdentity() {
  if (["-", "adhoc", "ad-hoc"].includes(process.env.BROWSER_LITE_SIGN_IDENTITY)) return null;
  if (process.env.BROWSER_LITE_SIGN_IDENTITY) return process.env.BROWSER_LITE_SIGN_IDENTITY;
  const output = execFileSync("security", ["find-identity", "-v", "-p", "codesigning"], {
    encoding: "utf8",
  });
  const identities = [...output.matchAll(/\"([^\"]+)\"/g)].map((match) => match[1]);
  return identities.find((value) => value.startsWith("Developer ID Application:"))
    || identities.find((value) => value.startsWith("Apple Development:"))
    || null;
}

async function signApp(appPath, identity) {
  run("codesign", [
    "--force", "--deep", "--sign", identity || "-", "--timestamp=none", "--options", "runtime",
    "--entitlements", join(ROOT, "build", "entitlements.plist"), appPath,
  ]);
}

async function prepareIcons() {
  const iconset = join(ROOT, "build", "BrowserLite.iconset");
  const renderedDir = join(ROOT, "build", "icon-render");
  await rm(iconset, { recursive: true, force: true });
  await rm(renderedDir, { recursive: true, force: true });
  await mkdir(iconset, { recursive: true });
  await mkdir(renderedDir, { recursive: true });
  run("qlmanage", ["-t", "-s", "1024", "-o", renderedDir, join(ROOT, "assets", "icon.svg")]);
  const sourcePng = join(renderedDir, "icon.svg.png");
  const sizes = [16, 32, 128, 256, 512];
  for (const size of sizes) {
    run("sips", ["-z", String(size), String(size), sourcePng, "--out", join(iconset, `icon_${size}x${size}.png`)]);
    run("sips", ["-z", String(size * 2), String(size * 2), sourcePng, "--out", join(iconset, `icon_${size}x${size}@2x.png`)]);
  }
  run("iconutil", ["-c", "icns", iconset, "-o", join(ROOT, "build", "BrowserLite.icns")]);
  run("qlmanage", ["-t", "-s", "36", "-o", renderedDir, join(ROOT, "assets", "trayTemplate.svg")]);
  await copyFile(join(renderedDir, "trayTemplate.svg.png"), join(ROOT, "assets", "trayTemplate.png"));
}

async function main() {
  const identity = signingIdentity();
  await rm(DIST, { recursive: true, force: true });
  await rm(TEMP, { recursive: true, force: true });
  await mkdir(DIST, { recursive: true });
  await prepareIcons();

  await packager({
    dir: ROOT,
    name: APP_NAME,
    platform: "darwin",
    arch: "arm64",
    ...(process.env.BROWSER_LITE_ELECTRON_ZIP_DIR
      ? { electronZipDir: process.env.BROWSER_LITE_ELECTRON_ZIP_DIR }
      : {}),
    out: DIST,
    overwrite: true,
    asar: true,
    prune: true,
    icon: join(ROOT, "build", "BrowserLite.icns"),
    appBundleId: "com.nodeskai.browserlite",
    appCategoryType: "public.app-category.productivity",
    extraResource: [
      join(ROOT, "assets", "trayTemplate.png"),
    ],
    ignore: ["^/dist($|/)", "^/test($|/)", "^/scripts($|/)", "^/build/dmg-staging($|/)"],
    extendInfo: {
      CFBundleDisplayName: APP_NAME,
      CFBundleName: APP_NAME,
      LSUIElement: false,
      LSApplicationCategoryType: "public.app-category.productivity",
      NSHumanReadableCopyright: "Copyright © 2026 Xy718",
      ...(identity ? {} : { LSEnvironment: { BROWSER_LITE_TEST_BUILD: "1" } }),
    },
  });

  const resourcesDir = join(APP_PATH, "Contents", "Resources");
  await copyFile(join(ROOT, "assets", "trayTemplate.png"), join(resourcesDir, "trayTemplate.png"));

  await signApp(APP_PATH, identity);
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", APP_PATH]);

  await mkdir(TEMP, { recursive: true });
  await cp(APP_PATH, join(TEMP, `${APP_NAME}.app`), {
    recursive: true,
    preserveTimestamps: true,
    verbatimSymlinks: true,
  });
  await symlink("/Applications", join(TEMP, "Applications"));
  run("hdiutil", ["create", "-volname", APP_NAME, "-srcfolder", TEMP, "-ov", "-format", "UDZO", DMG_PATH]);
  run("codesign", ["--force", "--sign", identity || "-", "--timestamp=none", DMG_PATH]);
  run("codesign", ["--verify", "--verbose=2", DMG_PATH]);
  run("hdiutil", ["verify", DMG_PATH]);

  const manifest = {
    app: APP_PATH,
    dmg: DMG_PATH,
    version: packageManifest.version,
    architecture: "arm64",
    signingMode: identity ? "development" : "ad-hoc",
    identity: identity || "ad-hoc",
    notarized: false,
    chromiumRuntime: "embedded Electron WebContentsView",
    notarizationReason: "Developer ID Application certificate and notary credentials are not configured",
  };
  await writeFile(join(DIST, "build-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`);
}

await main();
