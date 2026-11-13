import { defineConfig } from "@hey-api/openapi-ts";

// Generates web/src/api/ from web/openapi.json (exported from FastAPI by `npm run gen:api`).
// Never hand-edit web/src/api/ (AD-14); CI fails if regenerating changes it.
export default defineConfig({
  input: "./openapi.json",
  output: "src/api",
  plugins: [
    // createClientConfig() in src/lib/api-client.ts supplies the base URL.
    { name: "@hey-api/client-fetch", runtimeConfigPath: "./src/lib/api-client" },
    "@hey-api/typescript",
    "@hey-api/sdk",
  ],
});
