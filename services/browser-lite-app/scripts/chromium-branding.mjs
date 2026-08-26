import { readFile, readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

function dataPackLayout(buffer) {
  const version = buffer.readUInt32LE(0);
  if (![4, 5].includes(version)) return null;
  const resourceCount = version === 5 ? buffer.readUInt16LE(8) : buffer.readUInt32LE(4);
  const headerSize = version === 5 ? 12 : 9;
  const entries = [];
  for (let index = 0; index <= resourceCount; index += 1) {
    const position = headerSize + index * 6;
    if (position + 6 > buffer.length) return null;
    entries.push({
      id: buffer.readUInt16LE(position),
      offset: buffer.readUInt32LE(position + 2),
      position,
    });
  }
  if (!entries.length || entries[0].offset > buffer.length || entries.at(-1).offset > buffer.length) return null;
  return { entries };
}

function replaceAllBytes(buffer, search, replacement) {
  const parts = [];
  let cursor = 0;
  let match = buffer.indexOf(search, cursor);
  if (match < 0) return buffer;
  while (match >= 0) {
    parts.push(buffer.subarray(cursor, match), replacement);
    cursor = match + search.length;
    match = buffer.indexOf(search, cursor);
  }
  parts.push(buffer.subarray(cursor));
  return Buffer.concat(parts);
}

export function patchDataPackStrings(buffer, replacements) {
  const layout = dataPackLayout(buffer);
  if (!layout) return buffer;
  const resources = layout.entries.slice(0, -1).map((entry, index) => ({
    ...entry,
    data: buffer.subarray(entry.offset, layout.entries[index + 1].offset),
  }));
  let changed = false;
  for (const resource of resources) {
    for (const [from, to] of replacements) {
      const updated = replaceAllBytes(resource.data, Buffer.from(from), Buffer.from(to));
      if (updated !== resource.data) changed = true;
      resource.data = updated;
    }
  }
  if (!changed) return buffer;
  const prefix = Buffer.from(buffer.subarray(0, layout.entries[0].offset));
  let offset = prefix.length;
  resources.forEach((resource, index) => {
    prefix.writeUInt32LE(offset, layout.entries[index].position + 2);
    offset += resource.data.length;
  });
  prefix.writeUInt32LE(offset, layout.entries.at(-1).position + 2);
  return Buffer.concat([prefix, ...resources.map((resource) => resource.data)]);
}

async function visit(path, callback) {
  const entries = await readdir(path, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    const child = join(path, entry.name);
    if (entry.isDirectory()) await visit(child, callback);
    else if (entry.isFile()) await callback(child);
  }
}

export async function brandChromiumResources(appPath) {
  const replacements = [
    ["Google Chrome for Testing", "Browser Lite"],
    ["Chrome for Testing", "Browser Lite"],
  ];
  let patchedFiles = 0;
  await visit(join(appPath, "Contents"), async (path) => {
    if (path.endsWith(".pak")) {
      const original = await readFile(path);
      const branded = patchDataPackStrings(original, replacements);
      if (branded !== original) {
        await writeFile(path, branded);
        patchedFiles += 1;
      }
      return;
    }
    if (!path.endsWith("InfoPlist.strings")) return;
    const original = await readFile(path);
    let branded = original;
    for (const [from, to] of replacements) {
      branded = replaceAllBytes(branded, Buffer.from(from), Buffer.from(to));
    }
    if (!branded.equals(original)) {
      await writeFile(path, branded);
      patchedFiles += 1;
    }
  });
  return patchedFiles;
}
