from collections import OrderedDict
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, str], tuple[float, int]] = OrderedDict()
        self._lock = Lock()

    def check(self, request: Request, group: str, limit: int) -> None:
        host = request.client.host if request.client else "unknown"
        key = (group, host)
        with self._lock:
            now = monotonic()
            while self._entries:
                oldest = next(iter(self._entries))
                if self._entries[oldest][0] > now:
                    break
                self._entries.popitem(last=False)
            if key not in self._entries and len(self._entries) >= 4096:
                raise HTTPException(429, "Rate limiter is busy", headers={"Retry-After": "60"})
            reset, count = self._entries.get(key, (now + 60, 0))
            if count >= limit:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": str(int(reset - now) + 1)})
            self._entries[key] = (reset, count + 1)
