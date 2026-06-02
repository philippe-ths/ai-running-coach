"""Magic-link request rate limiter (ADR 0005)."""

import pytest

from app.core.config import settings
from app.services.auth import RateLimiter


class FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, name):
        self.store[name] = self.store.get(name, 0) + 1
        return self.store[name]

    def expire(self, name, seconds):
        return True


class BrokenRedis:
    def incr(self, name):
        raise RuntimeError("redis down")

    def expire(self, name, seconds):
        raise RuntimeError("redis down")


def test_allows_requests_under_the_limit(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_EMAIL_HOUR", 3)
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_IP_MINUTE", 10)
    limiter = RateLimiter(FakeRedis())

    for _ in range(3):
        assert limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0)


def test_blocks_when_per_email_limit_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_EMAIL_HOUR", 2)
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_IP_MINUTE", 100)
    limiter = RateLimiter(FakeRedis())

    assert limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0)
    assert limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0)
    assert not limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0)


def test_blocks_when_per_ip_limit_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_EMAIL_HOUR", 100)
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_IP_MINUTE", 1)
    limiter = RateLimiter(FakeRedis())

    # Different emails, same IP: the IP window still trips.
    assert limiter.allow_login_request(email="a@b.com", ip="9.9.9.9", now_epoch=1000.0)
    assert not limiter.allow_login_request(email="c@d.com", ip="9.9.9.9", now_epoch=1000.0)


def test_separate_windows_are_independent(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_EMAIL_HOUR", 1)
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_IP_MINUTE", 100)
    limiter = RateLimiter(FakeRedis())

    assert limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0)
    # Same email, next hour bucket -> allowed again.
    assert limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0 + 3600)


def test_fails_open_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_EMAIL_HOUR", 1)
    monkeypatch.setattr(settings, "LOGIN_RATE_PER_IP_MINUTE", 1)
    limiter = RateLimiter(BrokenRedis())

    # Login must remain available even when the rate-limit backend is down.
    for _ in range(5):
        assert limiter.allow_login_request(email="a@b.com", ip="1.1.1.1", now_epoch=1000.0)
