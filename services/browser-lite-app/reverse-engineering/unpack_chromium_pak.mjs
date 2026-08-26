#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { brotliDecompressSync, gunzipSync, inflateSync } from "node:zlib";

function parseDataPack(buffer) {
  const version = buffer.readUInt32LE(0);
  if (![4, 5].includes(version)) throw new Error(`Unsupported Chromium DataPack version: ${version}`);
  const encoding = buffer.readUInt8(version === 5 ? 4 : 8);
  const resourceCount = version === 5 ? buffer.readUInt16LE(8) : buffer.readUInt32LE(4);
  const aliasCount = version === 5 ? buffer.readUInt16LE(10) : 0;
  const headerSize = version === 5 ? 12 : 9;
  const entrySize = 6;
  const entries = [];
  for (let index = 0; index <= resourceCount; index += 1) {
    const position = headerSize + index * entrySize;
    entries.push({
      id: buffer.readUInt16LE(position),
      offset: buffer.readUInt32LE(position + 2),
    });
  }
  const resources = entries.slice(0, -1).map((entry, index) => ({
    id: entry.id,
    offset: entry.offset,
    size: entries[index + 1].offset - entry.offset,
    data: buffer.subarray(entry.offset, entries[index + 1].offset),
  }));
  return { version, encoding, resourceCount, aliasCount, resources };
}

function usage() {
  console.error("Usage: unpack_chromium_pak.mjs <resources.pak> [--find <text>] [--find-compressed] [--extract <id> <path>] [--decompress gzip|brotli|deflate]");
  process.exitCode = 2;
}

const [pakPath, ...args] = process.argv.slice(2);
if (!pakPath) {
  usage();
} else {
  const pack = parseDataPack(await readFile(pakPath));
  const output = {
    version: pack.version,
    encoding: pack.encoding,
    resourceCount: pack.resourceCount,
    aliasCount: pack.aliasCount,
    dataBytes: pack.resources.reduce((total, resource) => total + resource.size, 0),
  };

  const findIndex = args.indexOf("--find");
  if (findIndex >= 0) {
    const needle = Buffer.from(args[findIndex + 1] || "");
    if (!needle.length) throw new Error("--find requires non-empty text");
    output.matches = [];
    for (const resource of pack.resources) {
      if (resource.data.indexOf(needle) >= 0) {
        output.matches.push({ id: resource.id, offset: resource.offset, size: resource.size, encoding: "raw" });
        continue;
      }
      if (!args.includes("--find-compressed")) continue;
      for (const [encoding, decompress] of [
        ["brotli", brotliDecompressSync], ["gzip", gunzipSync], ["deflate", inflateSync],
      ]) {
        try {
          const decoded = decompress(resource.data);
          if (decoded.indexOf(needle) >= 0) {
            output.matches.push({
              id: resource.id,
              offset: resource.offset,
              size: resource.size,
              decodedSize: decoded.length,
              encoding,
            });
            break;
          }
        } catch {}
      }
    }
  }

  const extractIndex = args.indexOf("--extract");
  if (extractIndex >= 0) {
    const id = Number(args[extractIndex + 1]);
    const outputPath = args[extractIndex + 2];
    const resource = pack.resources.find((candidate) => candidate.id === id);
    if (!resource) throw new Error(`Resource not found: ${id}`);
    if (!outputPath) throw new Error("--extract requires an output path");
    const decodeIndex = args.indexOf("--decompress");
    const encoding = decodeIndex >= 0 ? args[decodeIndex + 1] : "raw";
    const decoder = { gzip: gunzipSync, brotli: brotliDecompressSync, deflate: inflateSync }[encoding];
    if (encoding !== "raw" && !decoder) throw new Error(`Unsupported decompression: ${encoding}`);
    const data = decoder ? decoder(resource.data) : resource.data;
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, data);
    output.extracted = { id, outputPath, size: data.length, encoding };
  }

  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
}
