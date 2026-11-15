"""Single writer with last-request-wins per channel.

Tracks pending intentions, never physical lamp state. Transport-agnostic:
``send`` is any coroutine taking a six-character command (HTTP or KNX).
See docs/lessons-learned.md for why a plain FIFO was not good enough.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
from typing import Awaitable, Callable


@dataclass(frozen=True)
class Macro:
    command: str
    targets: dict[int, int]


@dataclass
class Job:
    number: int
    label: str
    targets: dict[int, int]
    remaining: dict[int, int]
    macro: Macro | None
    resets: list[Macro]
    future: asyncio.Future
    started: float = field(default_factory=time.monotonic)
    sent: list[str] = field(default_factory=list)


class Writer:
    """All methods run on one event loop; admission has no await/race window.

    A cancelled caller cannot cancel the worker. A one-command native request
    runs at its original FIFO position, before any newer overlapping request.
    Optional resets inside multi-command jobs require full footprint ownership.
    """

    def __init__(self, send: Callable[[str], Awaitable[None]], completed=None):
        self.send = send
        self.completed = completed
        self.owners: dict[int, int] = {}
        self.pending: deque[Job] = deque()
        self.sequence = 0
        self.task: asyncio.Task | None = None
        self.active: Job | None = None
        self.closed = False
        self.history = deque(maxlen=80)
        self.error: str | None = None

    def owns(self, job, channels):
        return all(self.owners.get(c) == job.number for c in channels)

    def submit(self, targets, *, label='', macro=None, resets=()):
        if self.closed:
            raise RuntimeError('writer is shutting down')
        targets = dict(targets)
        if not targets or any(type(c) is not int or not 1 <= c <= 64 or
                              type(v) is not int or not 0 <= v <= 255
                              for c, v in targets.items()):
            raise ValueError('Expected channel 1..64 and value 0..255')
        if macro and macro.targets != targets:
            raise ValueError('Native command footprint must equal its target map')
        for reset in resets:
            if not reset.targets or not set(reset.targets) <= targets.keys() or any(reset.targets.values()):
                raise ValueError('Reset must be a zero-valued subset of request')
        self.sequence += 1
        job = Job(self.sequence, label, targets, dict(targets), macro,
                  list(resets), asyncio.get_running_loop().create_future())
        self.owners.update(dict.fromkeys(targets, job.number))
        # Remove superseded queued work now, not after draining a long FIFO.
        for old in list(self.pending):
            old.remaining = {c:v for c,v in old.remaining.items() if self.owns(old, [c])}
            if not old.remaining:
                self.pending.remove(old)
                self._finish(old, 'superseded')
        self.pending.append(job)
        self.error = None  # a fresh explicit request is allowed after a failure
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name='dalicontrol-writer')
        return job

    def _finish(self, job, status, error=None):
        result = {'request':job.number, 'label':job.label, 'status':status,
                  'seconds':round(time.monotonic()-job.started, 3),
                  'commands':list(job.sent), 'target_count':len(job.targets)}
        if error:
            result['error'] = error
        self.history.append(result)
        if not job.future.done():
            job.future.set_result(result)

    def _command(self, job):
        job.remaining = {c:v for c,v in job.remaining.items() if self.owns(job, [c])}
        if not job.remaining:
            return None
        macro, job.macro = job.macro, None
        if macro:
            # A macro is the FIRST and ONLY command of its job. Its FIFO
            # position precedes all newer jobs, so their overrides follow it.
            # Never expand native scene read-back into individual writes:
            # those values are not independently verified ECG memory.
            return macro.command, dict(job.remaining)
        # A reset is optional; retain exact individual fallback if unsafe or
        # not cheaper. Acknowledged zeros are removed only from this job.
        while job.resets:
            reset = job.resets.pop(0)
            scope = reset.targets.keys()
            if set(scope) <= job.remaining.keys() and self.owns(job, scope):
                zeros = {c:0 for c in scope if job.targets[c] == 0}
                if len(zeros) > 1:
                    return reset.command, zeros
        c = next(iter(job.remaining))
        return f'0B{c-1:02X}{job.remaining[c]:02X}', {c:job.remaining[c]}

    async def _run(self):
        try:
            while self.pending and not self.closed:
                job = self.pending.popleft()
                self.active = job
                command = self._command(job)
                if command is None:
                    self._finish(job, 'superseded')
                    continue
                payload, fulfilled = command
                try:
                    # Only this task invokes send. Never tied to caller task.
                    await self.send(payload)
                    job.sent.append(payload)
                    for c in fulfilled:
                        job.remaining.pop(c, None)
                except Exception as exc:
                    self.error = f'{type(exc).__name__}: {exc}'
                    self._finish(job, 'failed', self.error)
                    # Unknown output after a failed write: no dependent sparse
                    # continuation or automatic retry, no replay of queued jobs.
                    while self.pending:
                        self._finish(self.pending.popleft(), 'aborted', self.error)
                    break
                if self.closed and job.remaining:
                    self._finish(job, 'aborted', 'Writer shutdown')
                    continue
                if job.remaining and not self.closed:
                    self.pending.append(job)  # other rooms get a turn
                else:
                    status = 'completed' if self.owns(job, job.targets) else 'partial'
                    if self.completed is not None:
                        try:
                            await self.completed(job)
                        except Exception as exc:
                            self.error = f'Completion callback: {exc}'
                            self._finish(job, 'failed', self.error)
                            continue
                    self._finish(job, status)
        finally:
            self.active = None

    async def close(self):
        self.closed = True
        while self.pending:
            self._finish(self.pending.popleft(), 'aborted', 'Writer shutdown')
        if self.task:
            await asyncio.shield(self.task)  # finish the active command, never restart it

    def status(self):
        return {'active':self.active.number if self.active else None,
                'pending':len(self.pending), 'error':self.error,
                'history':list(self.history)}
