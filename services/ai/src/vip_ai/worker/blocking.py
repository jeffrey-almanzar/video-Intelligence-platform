"""Helpers for running CPU/GPU-bound work without starving the asyncio event loop.

BullMQ lock renewal and stalled-job checks run as tasks on the worker's event loop. A handler
that blocks the loop (FFmpeg via subprocess.run, Whisper inference, ...) stops renewal, the lock
expires and the job is re-delivered while still running. Always wrap such work in `run_blocking`.
"""

import asyncio
from collections.abc import Callable
from functools import partial


async def run_blocking[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    return await asyncio.to_thread(partial(fn, *args, **kwargs))
