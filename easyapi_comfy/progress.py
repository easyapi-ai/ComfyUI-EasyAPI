"""Use ComfyUI's native progress and text preview for remote node execution."""
import asyncio
import logging
from contextlib import suppress
from functools import partial, wraps
from time import monotonic


_STATUS_INTERVAL = 1.0
_LOGGER = logging.getLogger(__name__)


def _safe_send(send):
    def safe_send(text):
        try:
            send(text)
        except Exception:
            # UI feedback must never turn an already submitted generation into
            # an apparent failure. Do not log payloads or exception messages.
            _LOGGER.debug("ComfyUI progress text could not be sent")

    return safe_send


def _text_reporter():
    try:
        from comfy_execution.utils import get_executing_context
        from server import PromptServer
    except ModuleNotFoundError as error:
        if error.name not in {"comfy_execution", "comfy_execution.utils", "server"}:
            raise
        return None
    context = get_executing_context()
    server = getattr(PromptServer, "instance", None)
    if context is None or server is None or not server.client_id or not context.node_id:
        return None
    send = getattr(server, "send_progress_text", None)
    if send is None:
        return None
    # Capture both identifiers before yielding: another async node can run while
    # this one waits, so server.last_node_id must never be used here.
    return _safe_send(partial(send, node_id=context.node_id, sid=server.client_id))


def _status(state, started):
    return f"EasyAPI · {state} · 已用时 {max(0, int(monotonic() - started))}s"


async def _elapsed_status(send, started):
    while True:
        await asyncio.sleep(_STATUS_INTERVAL)
        send(_status("运行中", started))


def native_progress(execute):
    @wraps(execute)
    async def tracked(*args, **kwargs):
        try:
            from comfy.utils import ProgressBar
        except ModuleNotFoundError as error:
            if error.name not in {"comfy", "comfy.utils"}:
                raise
            # Offline protocol tools can import node helpers without ComfyUI.
            return await execute(*args, **kwargs)

        # A remote request has no local sampling steps or reliable ETA. Report
        # only its actual lifecycle: running, then finished after output decoding.
        # The ComfyUI hook resolves the node and prompt from the async context and
        # sends both legacy `progress` and current `progress_state` events.
        progress = ProgressBar(1)
        progress.update_absolute(0)
        started = monotonic()
        send = _text_reporter()
        ticker = None
        state = "未完成"
        if send:
            send(_status("运行中", started))
            ticker = asyncio.create_task(_elapsed_status(send, started))
        try:
            result = await execute(*args, **kwargs)
            progress.update_absolute(1)
            state = "已完成"
            return result
        except asyncio.CancelledError:
            state = "已停止"
            raise
        finally:
            if ticker:
                ticker.cancel()
                with suppress(asyncio.CancelledError):
                    await ticker
            if send:
                send(_status(state, started))

    return tracked
