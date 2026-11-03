"""Доступ: лимиты сообщений, пауза для повторных нарушителей, дневной бюджет токенов, роли."""
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class Verdict:
    allowed: bool
    reason: str | None = None     # rate_minute | rate_day | offender_pause | budget


class RateLimiter:
    def __init__(self, per_minute: int = 10, per_day: int = 100, clock=time.time):
        self.per_minute, self.per_day, self.clock = per_minute, per_day, clock
        self.events: dict[str, deque] = defaultdict(deque)

    def hit(self, user: str) -> Verdict:
        now, q = self.clock(), self.events[user]
        while q and now - q[0] > 86400:
            q.popleft()
        if len(q) >= self.per_day:
            return Verdict(False, "rate_day")
        if sum(1 for t in q if now - t <= 60) >= self.per_minute:
            return Verdict(False, "rate_minute")
        q.append(now)
        return Verdict(True)


class OffenderTracker:
    """3 подозрения на инъекцию за час → пауза 10 минут (рекомендация Anthropic про повторных нарушителей)."""

    def __init__(self, threshold: int = 3, window_s: int = 3600, pause_s: int = 600, clock=time.time):
        self.threshold, self.window_s, self.pause_s, self.clock = threshold, window_s, pause_s, clock
        self.flags: dict[str, deque] = defaultdict(deque)
        self.paused_until: dict[str, float] = {}

    def is_paused(self, user: str) -> bool:
        return self.clock() < self.paused_until.get(user, 0)

    def record(self, user: str) -> bool:
        """Отмечает подозрительное сообщение. True — пользователь ушёл на паузу."""
        now, q = self.clock(), self.flags[user]
        q.append(now)
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.threshold:
            self.paused_until[user] = now + self.pause_s
            q.clear()
            return True
        return False


class Budget:
    def __init__(self, daily_tokens: int, clock=time.time):
        self.daily_tokens, self.clock = daily_tokens, clock
        self.day, self.used = self._today(), 0

    def _today(self) -> int:
        return int(self.clock() // 86400)

    def _roll(self):
        if self._today() != self.day:
            self.day, self.used = self._today(), 0

    def available(self) -> bool:
        self._roll()
        return self.used < self.daily_tokens

    def spend(self, tokens: int) -> None:
        self._roll()
        self.used += max(0, tokens)


class Access:
    def __init__(self, admin_ids: set[str], public_debug: bool = False, daily_tokens: int = 2_000_000, clock=time.time):
        self.admin_ids, self.public_debug = admin_ids, public_debug
        self.rate = RateLimiter(clock=clock)
        self.offenders = OffenderTracker(clock=clock)
        self.budget = Budget(daily_tokens, clock=clock)

    def check(self, user: str) -> Verdict:
        if self.offenders.is_paused(user):
            return Verdict(False, "offender_pause")
        v = self.rate.hit(user)
        if not v.allowed:
            return v
        if not self.budget.available():
            return Verdict(False, "budget")
        return Verdict(True)

    def can_debug(self, user: str) -> bool:
        return self.public_debug or user in self.admin_ids

    def is_admin(self, user: str) -> bool:
        return user in self.admin_ids
