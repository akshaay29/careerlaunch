- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-project-scaffold-ci-and-deploy-pipeline.md`
  summary: Add a web test runner (e.g. vitest) and a test that renders HealthStatus against a stubbed /api/v1/health, asserting the ok and error states and that requests go to NEXT_PUBLIC_API_URL.
  evidence: Story 1.1 review (verification-gap) found no web tests at all; CI's web job runs only lint, typecheck and build, so the web→api wiring can break unnoticed. Deferred because web has no runner yet; best added in the first story with real web logic.
