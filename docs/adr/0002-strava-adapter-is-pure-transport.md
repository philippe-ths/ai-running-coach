# Strava HTTP adapter is pure transport

The previous `app/services/strava/client.py` mixed HTTP transport with persistence: `ensure_valid_token` performed an OAuth refresh and wrote new tokens back to `StravaAccount` in the same call. The mixing made the deep ingestion module impossible to test without a real database, and forced every test that touched ingestion to mock `httpx` ad hoc.

The new `StravaPort` is pure transport. `refresh_token(refresh_token: str) -> Tokens` returns plain data; the deep ingestion module owns expiry-check policy and persists new tokens through its own DB code. Two adapters now satisfy the port: `HTTPStravaAdapter` for production and `InMemoryStravaAdapter` for tests.

## Consequences

- Tests stop mocking `httpx` and inject `InMemoryStravaAdapter` instead.
- The HTTP adapter holds no `Session` reference and performs no DB writes.
- Adding a new adapter (e.g., a replay-from-fixture adapter for development) costs nothing.
- Future architecture reviews should not re-suggest pushing persistence back down into the adapter: the separation is deliberate, and the test gain is concrete.
