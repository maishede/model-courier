"""Run provider inference in a killable child process."""

from __future__ import annotations

import multiprocessing
import queue
import time
from pathlib import Path

from model_courier.contracts import ProviderResult, TaskEnvelope

from .provider import ExecutionContext, Provider, ProviderError


def _execute_child(
    provider: Provider,
    task_json: str,
    task_id: str,
    input_paths: list[str],
    deadline: float,
    result_queue: multiprocessing.Queue,
) -> None:
    try:
        task = TaskEnvelope.model_validate_json(task_json)
        context = ExecutionContext(
            task_id=task_id,
            deadline=deadline,
            input_paths=[Path(path) for path in input_paths],
        )
        result = provider.execute(task, context)
        result_queue.put(("ok", result.model_dump(mode="json", by_alias=True)))
    except ProviderError as exc:
        result_queue.put(("provider_error", exc.code, str(exc), exc.retryable))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)[:512]))
    finally:
        provider.close()


class ProcessRunner:
    def __init__(self, start_method: str = "spawn") -> None:
        self.context = multiprocessing.get_context(start_method)

    def execute(
        self,
        provider: Provider,
        task: TaskEnvelope,
        task_id: str,
        input_paths: list[Path],
        timeout: float,
    ) -> ProviderResult:
        result_queue = self.context.Queue()
        deadline = time.time() + timeout
        process = self.context.Process(
            target=_execute_child,
            args=(
                provider,
                task.model_dump_json(),
                task_id,
                [str(path) for path in input_paths],
                deadline,
                result_queue,
            ),
            daemon=True,
        )
        process.start()
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(2)
            raise ProviderError(
                "provider_timeout",
                "provider exceeded its execution deadline",
                retryable=True,
            )
        try:
            message = result_queue.get(timeout=1.0)
        except queue.Empty as exc:
            raise ProviderError(
                "provider_crashed",
                "provider process exited without a result",
                retryable=True,
            ) from exc
        finally:
            result_queue.close()
        if message[0] == "ok":
            return ProviderResult.model_validate(message[1])
        if message[0] == "provider_error":
            raise ProviderError(message[1], message[2], message[3])
        raise ProviderError("provider_error", message[2], retryable=True)
