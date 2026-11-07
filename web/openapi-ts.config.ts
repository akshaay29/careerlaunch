import { defineConfig } from "@hey-api/openapi-ts";

// Generates web/src/api/ from web/openapi.json (exported from FastAPI by `npm run gen:api`).
// Never hand-edit web/src/api/ (AD-14); CI fails if regenerating changes it.
export default defineConfig({
  input: "./openapi.json",
  output: "src/api",
  plugins: ["@hey-api/client-fetch", "@hey-api/typescript", "@hey-api/sdk"],
});
