# ModelCourier REST protocol v1

All endpoints are under `/v1` and require a scoped bearer token:

```http
Authorization: Bearer <device-or-worker-token>
```

Device and Worker tokens are separate. The server resolves `owner_id`, device,
and Worker identity from the token; clients cannot choose those fields in a
request.

## Device flow

1. `POST /v1/tasks` with a `TaskEnvelope`.
2. `PUT /v1/tasks/{task_id}/input?name=...&mime=...&size_bytes=...` with the raw bytes.
3. Poll `GET /v1/tasks/{task_id}`.
4. On `succeeded`, fetch `GET /v1/tasks/{task_id}/result`.
5. Use `POST /v1/tasks/{task_id}/cancel` to stop a task that has not completed.

The MVP accepts one input artifact per task. A request example:

```json
{
  "protocol_version": "1",
  "task_type": "audio.transcribe.v1",
  "input_artifacts": [
    {"name": "recording.wav", "mime": "audio/wav", "size_bytes": 43120}
  ],
  "options": {"language": "zh"},
  "requires": {},
  "idempotency_key": "pi-1:recording:2026-09-18T12:00:00Z",
  "ttl_seconds": 86400
}
```

The upload is streamed and only becomes `queued` after the server validates the
declared size and atomically moves the file into task storage. Reusing the same
idempotency key with a different request returns a conflict.

## Worker flow

1. `POST /v1/workers/register` with the Worker capability list.
2. `POST /v1/workers/poll` with `{"wait_seconds": 25}`.
3. Download `GET /v1/workers/tasks/{task_id}/input`.
4. Confirm `POST /v1/workers/tasks/{task_id}/start`.
5. Send `POST /v1/workers/tasks/{task_id}/heartbeat` while running.
6. Publish `PUT /v1/workers/tasks/{task_id}/result` or report `POST .../fail`.

Every lease-sensitive call includes `lease_generation` and `lease_token`.
Lease expiry, task expiry, cancellation, and a newer attempt make old calls
fail with a conflict. A Worker should discard the input and stop inference when
the server rejects a heartbeat.

## Status and errors

Normal states are `uploading`, `queued`, `leased`, `running`, `succeeded`,
`failed`, `expired`, and `cancelled`. `204 No Content` from Worker polling
means there is no matching task before the requested wait expires.

Errors use this shape when available:

```json
{
  "code": "lease_conflict",
  "message": "lease is no longer current",
  "retryable": false,
  "request_id": "req-..."
}
```

Retry network failures and `5xx` responses with backoff. Honor `409`, `413`,
`429`, and terminal task errors instead of retrying forever.
