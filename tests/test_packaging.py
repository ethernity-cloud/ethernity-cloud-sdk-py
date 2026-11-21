"""setup.py packages ethernity_cloud_sdk_py and its subpackages and not the
test suite: the wheel installs no top-level tests package. setup() is
replaced by a recorder of its arguments, so nothing is built."""
import os
import runpy

import setuptools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_packages_are_the_sdks_and_not_the_test_suite(monkeypatch):
    given = {}
    monkeypatch.setattr(setuptools, "setup", lambda **kwargs: given.update(kwargs))
    monkeypatch.chdir(ROOT)
    runpy.run_path(os.path.join(ROOT, "setup.py"))
    packages = given["packages"]
    assert "ethernity_cloud_sdk_py" in packages
    assert [package for package in packages if package == "tests" or package.startswith("tests.")] == []
    assert all(package == "ethernity_cloud_sdk_py" or package.startswith("ethernity_cloud_sdk_py.")
               for package in packages)
