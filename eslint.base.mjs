import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

const typescriptFiles = ["**/*.{ts,tsx,mts,cts}"];

export default defineConfig(
  {
    ignores: [
      "**/dist/",
      "**/build/",
      "**/coverage/",
      "**/cdk.out/",
      "**/.code-checks/",
      "**/.next/",
      "**/.expo/",
    ],
  },
  {
    files: ["**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}"],
    extends: [js.configs.recommended],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.browser, ...globals.node },
    },
  },
  {
    files: typescriptFiles,
    extends: [
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
    ],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
);
