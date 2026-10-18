# La Fabrique API & SSE Contract

- **Base URL:** `/api/v1`
- **Content types:** JSON APIs use `application/json`. SSE uses `text/event-stream`.
- **Date/time:** All timestamps are UTC ISO-8601 strings, e.g. `2025-01-01T12:00:00Z`.
- **IDs:** All resource IDs are string ULIDs, e.g. `run_01J5Z8Q...`, stable across restarts.

> The endpoint and schema tables in this document are generated from
> `docs/openapi/openapi.json`. Do not edit the generated tables by hand.
> Change the FastAPI/Pydantic definitions, then re-run:
>
>