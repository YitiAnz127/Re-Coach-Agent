import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
  },
  resolve: {
    alias: {
      // NodeNext style imports use .js extension but source is .ts
      // vitest handles this via the "resolve.extensions" and TS's
      // allowImportingTsExtensions behavior. We rely on vite's TS support.
    },
  },
});
