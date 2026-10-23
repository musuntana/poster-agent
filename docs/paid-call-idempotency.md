# Paid image-call idempotency

## Policy

The `ai` background mode can incur third-party model charges. Every request to `POST /api/posters` with `background_mode=ai` must include an `Idempotency-Key` header. The key must contain 8–128 ASCII letters, digits, underscores, or hyphens. A missing or empty key is rejected with HTTP 428 before reading/decoding uploaded images, claiming the ledger, or calling the image provider. Malformed keys are rejected with HTTP 422 before application-level image decoding or an execution-ledger claim.

Local-background requests may omit the header for legacy API compatibility, although the browser UI sends a key for every create request.

## Retry and recovery behavior

- **Same key + same normalized request, completed:** return the saved result; do not create another poster or call the provider again.
- **Same key + different normalized request:** return HTTP 409. Use a new key for a genuinely new request.
- **Key is already RUNNING or UNKNOWN:** return HTTP 409; do not automatically call the provider again.
- **Provider failure or uncertain response after the claim:** record `UNKNOWN`. Query `GET /api/executions/{key}` before taking further action. A client timeout does not prove that the provider did not complete or charge the request.
- **Missing key for AI mode:** return HTTP 428 and do not dispatch the paid call.

Do not automatically generate a replacement key after a timeout. A new key represents a new billable operation and can incur another charge.

## What this guarantees—and does not

The SQLite ledger atomically claims a key before provider dispatch and protects against duplicate calls that reach this single app instance with the same key. It is not cross-provider exactly-once execution: a process can fail after a provider accepted a request but before the result is persisted. Such cases must remain `UNKNOWN` rather than being retried automatically.

The ledger and locking model are local, single-worker only. This policy is not user authentication, resource authorization, multi-tenant isolation, a distributed lock, or a production job queue. Shared, reverse-proxy, multi-worker, and public deployments remain unsupported.

## Completed local artifact reconciliation

A poster created with an idempotency key now records a minimal recovery receipt
inside its final atomic `meta.json` write: the key, normalized request hash, and
SHA-256 checksums of the product image, optional logo, background, v1 PNG and
v1 specification. It does not save API keys or customer credentials in the receipt.

If a single-worker process stops **after** writing all artifacts but **before**
committing the `COMPLETED` ledger state, startup recovery verifies the receipt
and files against the ledger's stored request hash. A complete exact match can
be marked `COMPLETED` with a `RECOVERED_FROM_ARTIFACT` event; all other
abandoned `RUNNING` jobs remain `UNKNOWN`.

For an existing `UNKNOWN` record, use
`POST /api/executions/{key}/reconcile` or the history UI's
**核对本地产物** button. This only inspects local files and updates the SQLite
ledger when a unique, matching, intact artifact exists. It does not generate
a new image or call the paid model. Missing, corrupt, mismatched, ambiguous,
legacy-without-receipt, or revised artifacts remain `UNKNOWN`. The user must
not interpret an unresolved result as proof that the provider did not charge.

This is conservative single-worker recovery, not a distributed transaction,
full crash-durability guarantee, or cross-provider exactly-once billing.

## Execution history and browser retry lifecycle

The UI fingerprints uploaded file bytes with SHA-256 (the digest is stored locally, not the file), so files with the same name/size/timestamp but different contents are not treated as the same request. While a request is RUNNING or UNKNOWN, the UI reuses its key after retries. After a request is confirmed COMPLETED, an intentional new submit gets a fresh key even when the form and files are identical.

- `GET /api/executions?limit=10` returns a bounded list of recent execution summaries, including UNKNOWN records, timestamps, and minimal completed-poster identifiers. The limit must be between 1 and 50; the endpoint does not return full request/result specifications.
- The UI can query a listed key or restore a completed poster from the history list.
- The hourly guardrail backfills completed legacy AI runs from stored result metadata on first use. Older UNKNOWN rows without background-mode metadata cannot be classified reliably.

## Local Demo paid-call limits

The shared SQLite ledger limits paid AI generation to **one in-flight request** and **20 claimed attempts in a rolling hour** by default. Configure `POSTER_MAX_CONCURRENT_AI_GENERATIONS` (1–100) and `POSTER_MAX_AI_GENERATIONS_PER_HOUR` (1–10000) in `.env`. A rejected request receives HTTP 429 before a new execution record is created or the provider is called.

The hourly budget counts claims conservatively, including attempts that later become UNKNOWN; it is not a provider billing report. These are global single-instance guardrails, not per-user quotas, and they do not make the unsupported multi-worker startup/recovery model safe.

## Client example

```http
POST /api/posters
Idempotency-Key: poster-job-20261023-0001
Content-Type: multipart/form-data
```

Retain the same key when retrying the exact same logical request. If the server reports a conflict or unknown result, query the execution record first rather than blindly retrying.
