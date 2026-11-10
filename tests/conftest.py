"""Shared pytest setup: with CI_STRICT=1 a skipped test fails the run (see strict_skips.py)."""
from strict_skips import StrictSkips


def pytest_configure(config):
    StrictSkips.install(config)
