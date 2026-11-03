from bot.access import Access, Budget, OffenderTracker, RateLimiter


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_rate_limit_per_minute():
    clock = Clock()
    rl = RateLimiter(per_minute=3, per_day=100, clock=clock)
    assert [rl.hit("u").allowed for _ in range(4)] == [True, True, True, False]
    clock.t += 61
    assert rl.hit("u").allowed
    assert rl.hit("other").allowed  # лимит на пользователя, а не общий


def test_rate_limit_per_day():
    clock = Clock()
    rl = RateLimiter(per_minute=100, per_day=2, clock=clock)
    rl.hit("u"), rl.hit("u")
    assert rl.hit("u").reason == "rate_day"


def test_offender_pause():
    clock = Clock()
    ot = OffenderTracker(threshold=3, pause_s=600, clock=clock)
    assert [ot.record("u") for _ in range(3)] == [False, False, True]
    assert ot.is_paused("u")
    clock.t += 601
    assert not ot.is_paused("u")


def test_budget_resets_daily():
    clock = Clock()
    b = Budget(100, clock=clock)
    b.spend(100)
    assert not b.available()
    clock.t += 86400
    assert b.available()


def test_access_roles_and_checks():
    clock = Clock()
    a = Access({"42"}, public_debug=False, daily_tokens=10, clock=clock)
    assert a.can_debug("42") and not a.can_debug("7")
    assert Access(set(), public_debug=True).can_debug("7")
    assert a.check("7").allowed
    a.budget.spend(10)
    assert a.check("7").reason == "budget"
    for _ in range(3):
        a.offenders.record("8")
    assert a.check("8").reason == "offender_pause"
