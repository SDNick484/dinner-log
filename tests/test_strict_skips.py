"""strict_skips.py: under CI_STRICT=1 a skip fails the run, except where CI_ALLOW_SKIP or the contract rule allow it.

Each test runs a throwaway pytest session (pytester, in-process) with `-p strict_skips` and sets the three
variables itself, so CI's own CI_STRICT doesn't leak in.
"""
import pytest

PASSING = "def test_ok():\n    pass\n"
SKIPPING = "import pytest\n\ndef test_needs_thing():\n    pytest.skip('needs thing')\n"


@pytest.fixture
def run(pytester, monkeypatch):
    for name in ("CI_STRICT", "CI_ALLOW_SKIP", "CONTRACT_CATALOG_URL"):
        monkeypatch.delenv(name, raising=False)

    def go(strict=True, allow=None, contract_url=None):
        if strict:
            monkeypatch.setenv("CI_STRICT", "1")
        if allow is not None:
            monkeypatch.setenv("CI_ALLOW_SKIP", allow)
        if contract_url is not None:
            monkeypatch.setenv("CONTRACT_CATALOG_URL", contract_url)
        return pytester.runpytest("-p", "strict_skips")
    return go


def test_skips_are_fine_without_ci_strict(pytester, run):
    pytester.makepyfile(test_a=PASSING, test_b=SKIPPING)
    r = run(strict=False)
    assert r.ret == 0
    r.assert_outcomes(passed=1, skipped=1)


def test_a_skip_fails_the_run_and_is_named(pytester, run):
    pytester.makepyfile(test_a=PASSING, test_b=SKIPPING)
    r = run()
    assert r.ret == pytest.ExitCode.TESTS_FAILED
    r.stdout.fnmatch_lines(["*skipped under CI_STRICT=1*", "SKIPPED test_b.py::test_needs_thing: needs thing"])


def test_a_skipif_marker_counts(pytester, run):
    pytester.makepyfile(test_a=PASSING, test_b="import pytest\n\npytestmark = pytest.mark.skipif(True, reason='no sh')\n"
                                               "\ndef test_x():\n    pass\n")
    assert run().ret == pytest.ExitCode.TESTS_FAILED


def test_a_module_skipped_at_collection_counts(pytester, run):
    # test_access.py does this: pytest.importorskip("jwt") at module level
    pytester.makepyfile(test_a=PASSING,
                        test_b="import pytest\n\npytest.importorskip('no_such_module_xyz')\n\ndef test_x():\n    pass\n")
    r = run()
    assert r.ret == pytest.ExitCode.TESTS_FAILED
    r.stdout.fnmatch_lines(["SKIPPED test_b.py: could not import 'no_such_module_xyz'*"])


def test_only_skips_at_collection_still_fail(pytester, run):
    pytester.makepyfile(test_b="import pytest\n\npytest.importorskip('no_such_module_xyz')\n")
    assert run().ret == pytest.ExitCode.TESTS_FAILED


def test_allowed_globs_let_named_skips_through(pytester, run):
    pytester.makepyfile(test_a=PASSING, test_b=SKIPPING)
    r = run(allow="test_c.py::*, test_b.py::test_needs_*")
    assert r.ret == 0
    r.stdout.fnmatch_lines(["*skips allowed under CI_STRICT=1*", "ALLOWED test_b.py::test_needs_thing: needs thing"])


def test_allowed_globs_dont_cover_other_skips(pytester, run):
    pytester.makepyfile(test_a=SKIPPING, test_b=SKIPPING)
    r = run(allow="test_b.py::*")
    assert r.ret == pytest.ExitCode.TESTS_FAILED
    r.stdout.fnmatch_lines(["SKIPPED test_a.py::test_needs_thing: needs thing"])
    r.stdout.no_fnmatch_line("SKIPPED test_b.py*")


def test_globs_match_parametrized_ids(pytester, run):
    # the HEIC tests in test_photos.py are parametrized: test_x[with_heif] / test_x[without_heif]
    pytester.makepyfile(test_b="import pytest\n\n@pytest.mark.parametrize('m', ['with_heif', 'without_heif'])\n"
                               "def test_x(m):\n    if m == 'with_heif':\n        pytest.skip('no heif')\n")
    assert run(allow="test_b.py::*with_heif*").ret == 0
    assert run(allow="test_b.py::test_y*").ret == pytest.ExitCode.TESTS_FAILED


def test_xfail_is_not_a_skip(pytester, run):
    pytester.makepyfile(test_a="import pytest\n\n@pytest.mark.xfail(reason='known')\ndef test_x():\n    assert 0\n")
    assert run().ret == 0


def test_failures_still_fail(pytester, run):
    pytester.makepyfile(test_a="def test_x():\n    assert 0\n", test_b=SKIPPING)
    assert run(allow="test_b.py::*").ret == pytest.ExitCode.TESTS_FAILED


def test_contract_skips_are_allowed_until_a_catalog_is_configured(pytester, run):
    pytester.makepyfile(test_a=PASSING, **{"tests/contract/test_c": SKIPPING})
    assert run().ret == 0
    r = run(contract_url="http://127.0.0.1:8767")
    assert r.ret == pytest.ExitCode.TESTS_FAILED
    r.stdout.fnmatch_lines(["SKIPPED tests/contract/test_c.py::test_needs_thing: needs thing"])
