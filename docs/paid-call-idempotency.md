# Paid image-call idempotency

## Policy

The `ai` background mode can incur third-party model charges. Every request to `POST /api/posters` with `background_mode=ai` must include an `Idempotency-Key` header. The key must contain 8–128 ASCII letters, digits, underscores, or hyphens. A missing key is rejected with HTTP 428 before the image provider is called. Invalid keys are rejected with HTTP 422.

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

## Client example

```http
POST /api/posters
Idempotency-Key: poster-job-20261023-0001
Content-Type: multipart/form-data
```

Retain the same key when retrying the exact same logical request. If the server reports a conflict or unknown result, query the execution record first rather than blindly retrying.
