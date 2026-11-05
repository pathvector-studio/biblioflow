import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // eslint-config-next 16 (react-hooks 7) で増えた規則。localStorage をハイドレーション後に読む・
      // 絞り込みが変わったら選択を捨てる、といった意図的な使い方があるため、警告に留める。
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "public/pdf.worker.min.mjs"]),
]);
