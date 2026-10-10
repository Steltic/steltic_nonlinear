"""Test-suite temp isolation (NL-R2-31).

Many tests (and code under test, e.g. pushover.package_reader.locate() unpacking a .zip job) create folders with
tempfile.mkdtemp() and never remove them; a full run used to leave ~60 copies of the 18 MB Ex22 fixture plus other job
copies in TMPDIR (3,216 folders / 21 GB had accumulated on the shared machine).

Every tempfile.* call and every child process (TMPDIR/TEMP/TMP) is pointed at a pytest-managed folder:
  * a session folder (module/session fixtures, collection-time code), removed at the end of the session;
  * a per-test folder under the test's tmp_path, removed when the test passes
    (tmp_path_retention_policy = "failed" in pyproject.toml: a failing test keeps its files for debugging).
Without --basetemp pytest's own root (TMPDIR/pytest-of-<user>/pytest-N) is removed when all tests pass, and the empty
pytest-of-<user> folder is removed below, so a passing run leaves nothing in TMPDIR."""
import os
import shutil
import tempfile

import pytest

_ENV = ("TMPDIR", "TEMP", "TMP")
_ORIG_TEMPROOT = tempfile.gettempdir()             # the real TMPDIR, before any redirection


def _redirect(mp, path):
    path = str(path)
    mp.setattr(tempfile, "tempdir", path)
    for k in _ENV:
        mp.setenv(k, path)


@pytest.fixture(scope="session", autouse=True)
def _session_tempdir(tmp_path_factory):
    d = tmp_path_factory.mktemp("session_tmp")
    with pytest.MonkeyPatch.context() as mp:
        _redirect(mp, d)
        yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _test_tempdir(tmp_path, monkeypatch):
    d = tmp_path / "tmp"
    d.mkdir()
    _redirect(monkeypatch, d)
    yield d


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("basetemp", None):
        return                                       # the caller owns --basetemp
    try:
        import getpass
        user = getpass.getuser()
    except Exception:                                # noqa: BLE001 -- pytest falls back to "unknown" too
        user = "unknown"
    root = os.path.join(_ORIG_TEMPROOT, "pytest-of-%s" % user)
    try:
        os.rmdir(root)                               # only when empty: another session's runs are left alone
    except OSError:
        pass
