"""setup.py packages ethernity_cloud_sdk_py and its subpackages and not the
test suite: the wheel installs no top-level tests package. The release is
0.10.8, a release of the PoX 2.0 line, and it requires runner-py 0.5.38 or
later. setup() is replaced by a recorder of its arguments, so nothing is
built."""
import os
import runpy

import setuptools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup_arguments(monkeypatch):
    given = {}
    monkeypatch.setattr(setuptools, "setup", lambda **kwargs: given.update(kwargs))
    monkeypatch.chdir(ROOT)
    runpy.run_path(os.path.join(ROOT, "setup.py"))
    return given


def test_the_release_is_0_10_8_and_requires_runner_0_5_38_or_later(monkeypatch):
    given = _setup_arguments(monkeypatch)
    assert given["version"] == "0.10.8"
    runners = [requirement for requirement in given["install_requires"]
               if requirement.startswith("ethernity-cloud-runner-py")]
    assert runners == ["ethernity-cloud-runner-py>=0.5.38"]


def test_the_packages_are_the_sdks_and_not_the_test_suite(monkeypatch):
    packages = _setup_arguments(monkeypatch)["packages"]
    assert "ethernity_cloud_sdk_py" in packages
    assert [package for package in packages if package == "tests" or package.startswith("tests.")] == []
    assert all(package == "ethernity_cloud_sdk_py" or package.startswith("ethernity_cloud_sdk_py.")
               for package in packages)
