"""The one rate limiter: registration, setup and sign-in each keep their
counts in it, by name. Per process, so each worker allows its own attempts
(DEPLOYMENT.md)."""

from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

limiter = MovingWindowRateLimiter(MemoryStorage())
