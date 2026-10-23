# Paid image generation idempotency and recovery

## Why this is required

The `ai` background mode can invoke a paid image provider. Every such request must include an `Idempotency-Key`. The API rejects a missing or empty key with HTTP 428 before reading or decoding uploaded image bytes, claiming an execution, or calling the provider.

The key must contain 8–128 ASCII letters, digits, underscores, or hyphens. Invalid syntax returns HTTP 422. Local deterministic rendering remains backward-compatible without the header.

## Client rules

- Generate one random key for a new logical AI generation request.
- Reuse the same key when retrying the exact same request after a network interruption. Do not mint a new key merely because the client did not receive a response.
- The web UI fingerprints uploaded file bytes with SHA-256; it stores the digest, not the file. This avoids treating different files with identical names, sizes, and timestamps as the same request.
- After a request is confirmed COMPLETED, an intentional new submit—even with identical fields and files—gets a new key and starts a new generation.
- While a request is RUNNING or UNKNOWN, reuse its key and query status before deciding whether to start another paid operation.
- If request fields or uploaded file bytes change, use a new key.

## Server behavior

| Condition | Behavior |
| --- | --- |
| AI mode, key missing or empty | HTTP 428 before reading upload bytes, ledger claim, or provider call |
| Invalid key syntax | HTTP 422 |
| Same key and same input, prior result completed | Return the original result; do not generate another poster |
| Same key with different input | HTTP 409 |
| Same key is RUNNING or UNKNOWN | HTTP 409; do not automatically retry a potentially charged provider call |
| Provider error after claim | HTTP 502 and ledger status UNKNOWN |
| Local paid-call concurrency or rolling hourly cap reached | HTTP 429 before creating a new execution claim or calling the provider |

## Execution history and recovery

- `GET /api/executions/{key}` retrieves a single execution status.
- `GET /api/executions?limit=10` returns a bounded list of recent execution summaries. The limit is 1–50.
- History contains the idempotency key, status, timestamps, and minimal completed-poster identifiers. It does not expose the full request specification or result payload.
- The local UI can inspect recent execution states and restore a completed poster.
- Do not blindly retry an UNKNOWN result. The provider may have accepted the request even if the application lost its response; inspect the execution record and provider account before manually deciding whether to create a new operation.

## Local Demo paid-call limits

The SQLite ledger defaults to one in-flight paid AI generation and 20 claimed attempts in a rolling hour. Configure these values in `.env`:

- `POSTER_MAX_CONCURRENT_AI_GENERATIONS`: integer 1–100; default `1`.
- `POSTER_MAX_AI_GENERATIONS_PER_HOUR`: integer 1–10000; default `20`.

The hourly budget counts claimed attempts conservatively, including attempts that later become UNKNOWN. It is not a provider billing report. These are global single-instance guardrails, not per-user quotas. Rejected requests receive HTTP 429 without a new execution record/provider call.

## Guarantees and limits

The SQLite execution ledger protects against duplicate claims within the supported local, single-process deployment model. It does not provide provider-side exactly-once billing. A process may fail after a provider accepted a request but before the result is persisted; such cases remain UNKNOWN rather than being automatically retried.

The history endpoint and limits are not authentication, resource authorization, multi-tenant isolation, production rate limiting, a distributed lock, or a production task queue. Multi-worker, reverse-proxy, shared, and public deployments remain unsupported.
