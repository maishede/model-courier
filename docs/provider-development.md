# Provider development

Providers translate a local model's native API into a stable ModelCourier task
and result schema. The core Worker owns polling, file download, leases, and
result publication; a Provider only validates and executes a task.

Implement the four methods below:

```python
class MyProvider:
    def describe(self) -> CapabilityManifest: ...
    def validate(self, task: TaskEnvelope) -> None: ...
    def execute(self, task: TaskEnvelope, context: ExecutionContext) -> ProviderResult: ...
    def close(self) -> None: ...
```

Return a `CapabilityManifest` with a unique capability ID, a versioned task
type, supported input MIME types, and model identifiers. `validate` must reject
the wrong task type or unsupported options. `execute` receives local input
paths and returns a `ProviderResult`; use `canonical_json_digest(payload)` for
the result digest. Never return raw framework objects or a model-specific
schema to the device.

Register a factory through the `model_courier.providers` entry-point group:

```toml
[project.entry-points."model_courier.providers"]
my_provider = "my_package.provider:registration"
```

The registration function returns `ProviderRegistration(capability, factory)`.
Keep framework imports lazy so the package can be inspected without downloading
weights or requiring a GPU. Restrict configuration to local model IDs and
runtime settings. Do not accept an import path, arbitrary command, remote model
URL, or a file path supplied by the device.

The default Worker executes Provider code in a child process with an execution
deadline. Keep `close()` idempotent, treat missing model dependencies as a
retryable `ProviderError`, and use `context.report_progress()` at useful
boundaries. Provider processes are intentionally isolated; install conflicting
model frameworks into separate Worker virtual environments when necessary.

Add contract tests that import the adapter without optional dependencies and
fake the model object for output normalization. Review the model and dependency
licenses before distributing an adapter. The public server must depend only on
the core package, never on a model framework.
