// Supply-chain check for the browser extension (specs/security-baseline.md R6). Run after `node extension/build.mjs`.
// 1. The extension has no dependencies: no dependency fields in package.json, no lock file or node_modules,
//    and its scripts import only relative files (the build and tests may also use node: built-ins).
// 2. Each built zip holds only the files we ship: manifest.json, the popup and options pages, ui.css,
//    src/*.js and _locales/<lang>/messages.json. No source maps, tests, dotfiles or anything else.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..");
const ext = join(repo, "extension");
const errors = [];

const pkg = JSON.parse(readFileSync(join(ext, "package.json"), "utf8"));
for (const field of ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "bundleDependencies"]) {
  if (pkg[field] && Object.keys(pkg[field]).length) errors.push(`extension/package.json has ${field}`);
}
for (const name of ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "node_modules"]) {
  if (existsSync(join(ext, name))) errors.push(`extension/${name} exists`);
}

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (n === "dist" || n === "node_modules") return [];
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
const IMPORT = /(?:^|[;\s])(?:import|export)\s[^"'`]*?from\s*["']([^"']+)["']|(?:^|[;\s])import\s*["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/gm;
for (const file of walk(ext).filter((f) => /\.(m?js)$/.test(f))) {
  const rel = relative(ext, file);
  const tooling = rel === "build.mjs" || rel === "manifest.mjs" || rel.startsWith("test/");
  for (const m of readFileSync(file, "utf8").matchAll(IMPORT)) {
    const spec = m[1] ?? m[2] ?? m[3];
    if (spec.startsWith("./") || spec.startsWith("../")) continue;
    if (tooling && spec.startsWith("node:")) continue;
    errors.push(`extension/${rel} imports "${spec}"`);
  }
}

// Names in a zip's central directory.
function zipNames(path) {
  const buf = readFileSync(path);
  const end = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (end < 0) throw new Error(`${path}: not a zip`);
  const count = buf.readUInt16LE(end + 10);
  let p = buf.readUInt32LE(end + 16);
  const names = [];
  for (let i = 0; i < count; i++) {
    if (buf.readUInt32LE(p) !== 0x02014b50) throw new Error(`${path}: bad central directory`);
    const n = buf.readUInt16LE(p + 28), x = buf.readUInt16LE(p + 30), c = buf.readUInt16LE(p + 32);
    names.push(buf.subarray(p + 46, p + 46 + n).toString("utf8"));
    p += 46 + n + x + c;
  }
  return names;
}
const ALLOWED = [
  /^manifest\.json$/,
  /^(popup|options)\.(html|js)$/,
  /^ui\.css$/,
  /^src\/[a-z][a-z0-9-]*\.js$/,
  /^_locales\/[a-z]{2}(_[A-Z]{2})?\/messages\.json$/,
];
const version = readFileSync(join(repo, "VERSION"), "utf8").trim();
for (const browser of ["chrome", "firefox"]) {
  const zip = join(ext, "dist", `biblioflow-clipper-${browser}-${version}.zip`);
  if (!existsSync(zip)) {
    errors.push(`${relative(repo, zip)} missing (run node extension/build.mjs first)`);
    continue;
  }
  const names = zipNames(zip);
  for (const name of names) {
    if (!ALLOWED.some((re) => re.test(name))) errors.push(`${relative(repo, zip)} contains ${name}`);
  }
  if (!names.includes("manifest.json")) errors.push(`${relative(repo, zip)} has no manifest.json`);
  console.log(`${browser}: ${names.length} files`);
}

if (errors.length) {
  for (const e of errors) console.error(`::error::${e}`);
  process.exit(1);
}
console.log("extension: no dependencies, zips contain only shipped files");
