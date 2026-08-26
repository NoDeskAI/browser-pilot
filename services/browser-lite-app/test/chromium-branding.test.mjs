import assert from "node:assert/strict";
import { test } from "node:test";

import { patchDataPackStrings } from "../scripts/chromium-branding.mjs";

test("Chromium locale packs replace testing branding and preserve resource indexes", () => {
  const resource = Buffer.from("自定义 Chrome for Testing");
  const pack = Buffer.alloc(24 + resource.length);
  pack.writeUInt32LE(5, 0);
  pack.writeUInt8(1, 4);
  pack.writeUInt16LE(1, 8);
  pack.writeUInt16LE(0, 10);
  pack.writeUInt16LE(1123, 12);
  pack.writeUInt32LE(24, 14);
  pack.writeUInt16LE(0, 18);
  pack.writeUInt32LE(24 + resource.length, 20);
  resource.copy(pack, 24);

  const branded = patchDataPackStrings(pack, [["Chrome for Testing", "Browser Lite"]]);
  const start = branded.readUInt32LE(14);
  const end = branded.readUInt32LE(20);
  assert.equal(start, 24);
  assert.equal(end, branded.length);
  assert.equal(branded.subarray(start, end).toString(), "自定义 Browser Lite");
  assert.equal(branded.includes(Buffer.from("Chrome for Testing")), false);
});
