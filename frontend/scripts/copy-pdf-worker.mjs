// pdf.js の worker を同一オリジンから配信するため public/ にコピーする（CDN は使わない）。
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
mkdirSync(resolve(root, "public"), { recursive: true });
copyFileSync(
  resolve(root, "node_modules/pdfjs-dist/build/pdf.worker.min.mjs"),
  resolve(root, "public/pdf.worker.min.mjs"),
);
