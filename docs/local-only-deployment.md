# Local-only deployment boundary

## Supported launch

Poster Agent currently has no authentication, resource-level authorization, or multi-tenant isolation. The supported server entry point is a local-only launcher:

```bash
uv run --locked python -m app.run_local --port 8000
# For local development only:
uv run --locked python -m app.run_local --reload --port 8000
```

The launcher accepts only numeric loopback addresses such as `127.0.0.1` and `::1`; it rejects `0.0.0.0`, non-loopback addresses, and hostnames. It disables Uvicorn proxy-header trust. As defense in depth, application middleware rejects non-loopback peers and non-local `Host` values, and rejects cross-origin or cross-site browser requests, including reads that carry browser origin metadata. Open the UI at <http://127.0.0.1:8000>. These checks reduce accidental exposure and browser-based request risks, but they are not authentication.

## Explicitly unsupported

- Do not bind the unauthenticated application to a LAN or public interface.
- Do not run `uvicorn app.main:app --host 0.0.0.0` or another raw Uvicorn command as a substitute for the launcher. Direct Uvicorn invocation bypasses launcher bind-address validation, although the application-level peer check still rejects ordinary non-loopback direct requests.
- Do not put the app behind a reverse proxy, expose it through container port publishing, or treat a firewall as a replacement for application authentication.
- Do not process customer data unless you have authorization and an appropriate security boundary.

The launcher is a guardrail against accidental exposure through the documented startup path, not a complete security boundary. A caller who deliberately uses a different ASGI server invocation can bypass it. M7 remains open until authentication, resource authorization, rate/concurrency limits, and supported persistence/worker semantics are designed and tested.

## Validation

The unit tests cover accepted loopback addresses, rejected network and hostname binds, port validation, Uvicorn arguments, and refusal before the server starts. They do not constitute a penetration test or production security certification.
