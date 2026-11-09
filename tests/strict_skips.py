"""CI_STRICT=1: a skipped test fails the run.

Some tests skip quietly when something they need is missing (PyJWT, pillow_heif, sh/tar/base64, a git
checkout). On a laptop that's convenient; in CI it hides a broken feature. With CI_STRICT=1 (CI sets it) every
skip is recorded, the run exits 1, and the summary lists each one with its reason. Exceptions:

  CI_ALLOW_SKIP     comma-separated fnmatch globs (* and ?) on node ids that may skip in this job, e.g.
                    tests/test_photos.py::test_real_heic_* where pillow_heif isn't installed
  tests/contract/   may skip unless CONTRACT_CATALOG_URL is set: those need a running Cookbook Collection

xfail doesn't count. Node ids are relative to the repository (pytest.ini pins the rootdir), so a glob reads the
same however pytest is invoked. tests/conftest.py installs this; tests/test_strict_skips.py loads it with
`-p strict_skips`.
"""
import fnmatch
import os

import pytest

CONTRACT_PREFIX = "tests/contract/"


def globs(raw):
    return [g.strip() for g in raw.replace("\n", ",").split(",") if g.strip()]


def skip_reason(report):
    if isinstance(report.longrepr, tuple):  # (path, lineno, "Skipped: why")
        reason = str(report.longrepr[2])
    else:
        reason = str(report.longrepr)
    return reason.removeprefix("Skipped: ")


class StrictSkips:
    def __init__(self, allow=(), contract_live=False):
        self.allow = list(allow)
        self.contract_live = contract_live
        self.refused = []   # (node id, reason)
        self.allowed = []

    @classmethod
    def install(cls, config):
        """Register for this run when CI_STRICT=1; otherwise do nothing."""
        if os.environ.get("CI_STRICT", "").strip() != "1":
            return None
        plugin = cls(globs(os.environ.get("CI_ALLOW_SKIP", "")),
                     contract_live=bool(os.environ.get("CONTRACT_CATALOG_URL", "").strip()))
        config.pluginmanager.register(plugin, "strict-skips")
        return plugin

    def permitted(self, nodeid):
        if nodeid.startswith(CONTRACT_PREFIX) and not self.contract_live:
            return True
        return any(fnmatch.fnmatchcase(nodeid, g) for g in self.allow)

    def record(self, report):
        if not report.skipped or hasattr(report, "wasxfail"):
            return
        entry = (report.nodeid, skip_reason(report))
        (self.allowed if self.permitted(report.nodeid) else self.refused).append(entry)

    def pytest_runtest_logreport(self, report):
        self.record(report)

    def pytest_collectreport(self, report):   # a whole module skipped, e.g. pytest.importorskip at the top
        self.record(report)

    def pytest_sessionfinish(self, session):
        if self.refused and session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        tr = terminalreporter
        if self.allowed:
            tr.section("skips allowed under CI_STRICT=1")
            for nodeid, reason in self.allowed:
                tr.line(f"ALLOWED {nodeid}: {reason}")
        if self.refused:
            tr.section("skipped under CI_STRICT=1 (fails the run)", red=True)
            for nodeid, reason in self.refused:
                tr.line(f"SKIPPED {nodeid}: {reason}", red=True)
            tr.line("Install what the test needs, or add its node id to CI_ALLOW_SKIP for this job.")


def pytest_configure(config):   # for `-p strict_skips`
    StrictSkips.install(config)
