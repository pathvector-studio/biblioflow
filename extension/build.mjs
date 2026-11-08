// Builds extension/dist/{chrome,firefox}/ and biblioflow-clipper-<browser>-<version>.zip. No dependencies.
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateRawSync } from "node:zlib";
import { buildManifest } from "./manifest.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, "dist");
const FILES = ["popup.html", "popup.js", "options.html", "options.js", "ui.css", "src", "_locales"];

export function readVersion() {
  const raw = readFileSync(join(root, "..", "VERSION"), "utf8").trim();
  const m = /^(\d+)\.(\d+)\.(\d+)/.exec(raw);
  if (!m) throw new Error(`VERSION is not x.y.z: ${raw}`);
  return `${m[1]}.${m[2]}.${m[3]}`;
}

const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};

function walk(dir) {
  return readdirSync(dir)
    .sort()
    .flatMap((n) => {
      const p = join(dir, n);
      return statSync(p).isDirectory() ? walk(p) : [p];
    });
}

// Minimal zip writer (deflate, fixed timestamp so the output is reproducible).
export function zipDir(dir) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const file of walk(dir)) {
    const name = Buffer.from(relative(dir, file).split("\\").join("/"));
    const data = readFileSync(file);
    const comp = deflateRawSync(data);
    const crc = crc32(data);
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0);
    lh.writeUInt16LE(20, 4);
    lh.writeUInt16LE(0x0800, 6); // UTF-8 names
    lh.writeUInt16LE(8, 8);
    lh.writeUInt16LE(0, 10);
    lh.writeUInt16LE(0x21, 12); // 1980-01-01
    lh.writeUInt32LE(crc, 14);
    lh.writeUInt32LE(comp.length, 18);
    lh.writeUInt32LE(data.length, 22);
    lh.writeUInt16LE(name.length, 26);
    locals.push(lh, name, comp);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0);
    ch.writeUInt16LE(20, 4);
    ch.writeUInt16LE(20, 6);
    ch.writeUInt16LE(0x0800, 8);
    ch.writeUInt16LE(8, 10);
    ch.writeUInt16LE(0, 12);
    ch.writeUInt16LE(0x21, 14);
    ch.writeUInt32LE(crc, 16);
    ch.writeUInt32LE(comp.length, 20);
    ch.writeUInt32LE(data.length, 24);
    ch.writeUInt16LE(name.length, 28);
    ch.writeUInt32LE(offset, 42);
    centrals.push(ch, name);
    offset += lh.length + name.length + comp.length;
  }
  const centralSize = centrals.reduce((n, b) => n + b.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(centrals.length / 2, 8);
  end.writeUInt16LE(centrals.length / 2, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, ...centrals, end]);
}

export function build() {
  const version = readVersion();
  rmSync(dist, { recursive: true, force: true });
  const out = [];
  for (const browser of ["chrome", "firefox"]) {
    const dir = join(dist, browser);
    mkdirSync(dir, { recursive: true });
    for (const f of FILES) cpSync(join(root, f), join(dir, f), { recursive: true });
    writeFileSync(join(dir, "manifest.json"), JSON.stringify(buildManifest(browser, version), null, 2) + "\n");
    const zip = join(dist, `biblioflow-clipper-${browser}-${version}.zip`);
    writeFileSync(zip, zipDir(dir));
    out.push(zip);
  }
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const z of build()) console.log(relative(process.cwd(), z));
}
