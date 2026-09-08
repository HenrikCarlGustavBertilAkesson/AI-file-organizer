"""Shared, bounded API requests and usage accounting (including nested agent classification)."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, asdict
import random
import time

CURRENT = ContextVar('ai_usage', default=None)


class AILimitReached(Exception):
    pass


def bounded_int(value, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f'{label} must be an integer between 1 and {maximum}.')
    return value


@dataclass
class Usage:
    max_attempts: int
    attempts: int = 0
    responses: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unreported_attempts: int = 0


class Budget:
    def __init__(self, limit, report=None, progress=None):
        self.usage = Usage(limit)
        self.report = report
        self.progress = progress

    def publish(self):
        if self.report:
            self.report(asdict(self.usage))

    def checkpoint(self):
        if self.progress:
            self.progress()

    def request(self, function, **kwargs):
        for attempt in range(3):
            self.checkpoint()
            if self.usage.attempts >= self.usage.max_attempts:
                raise AILimitReached('AI request limit reached. Start a new batch to continue.')
            self.usage.attempts += 1
            if attempt:
                self.usage.retries += 1
            self.usage.unreported_attempts += 1
            self.publish()
            try:
                response = function(**kwargs)
            except Exception as error:
                status = getattr(error, 'status_code', None)
                transient = status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
                transient = transient or type(error).__name__ in ('APIConnectionError', 'APITimeoutError')
                if not transient or attempt == 2:
                    raise
                # Short exponential backoff plus jitter; cancellation stays responsive.
                delay = 2 ** attempt + random.random() * 0.25
                headers = getattr(getattr(error, 'response', None), 'headers', {})
                try:
                    delay = max(delay, min(30, float(headers.get('retry-after', 0))))
                except (ValueError, TypeError):
                    pass
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    self.checkpoint()
                    time.sleep(min(0.1, max(0, deadline-time.monotonic())))
                continue
            self.usage.responses += 1
            usage = getattr(response, 'usage', None)
            if usage is not None:
                self.usage.input_tokens += usage.input_tokens
                self.usage.output_tokens += usage.output_tokens
                self.usage.unreported_attempts -= 1
            self.publish()
            return response


@contextmanager
def usage_scope(limit, report=None, progress=None):
    existing = CURRENT.get()
    if existing is not None:
        yield existing
        return
    budget = Budget(limit, report, progress)
    token = CURRENT.set(budget)
    try:
        yield budget
    finally:
        budget.publish()
        CURRENT.reset(token)


def api_request(function, **kwargs):
    with usage_scope(3) as budget:
        return budget.request(function, **kwargs)
