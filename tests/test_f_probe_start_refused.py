"""start_probe of securelock.py.tmpl keeps the probe only once its ready
object is written (PHASE-D-PLAN.md F.1: no beat when no key exists): a
refused ready put raises with self.probe still None, so _report_failure's
end_probe beats no idle tail under a key the trustedzone never received;
an accepted one leaves the started probe on the app; an unbound task
starts none."""
from types import SimpleNamespace

import pytest

from tests.f_session_support import CHALLENGE, ORDER_ID
from tests.test_f_securelock_boot import _compile


class _Probe:
    def __init__(self, app, challenge, order_id):
        self.args = (app, challenge, order_id)
        self.started = False

    def start(self):
        if self.args[0].refuse_ready:
            raise RuntimeError("securelock.ready refused")
        self.started = True


def _app(binding, refuse_ready):
    return SimpleNamespace(task_binding=binding, probe=None, refuse_ready=refuse_ready)


def test_a_refused_ready_put_raises_and_leaves_no_probe_on_the_app():
    cls = _compile(["start_probe"], namespace={"securelock_probe": SimpleNamespace(SecurelockProbe=_Probe)})
    binding = {"order_id": ORDER_ID, "challenge": CHALLENGE}
    app = _app(binding, refuse_ready=True)
    with pytest.raises(RuntimeError, match="securelock.ready refused"):
        cls.start_probe(app)
    assert app.probe is None
    app = _app(binding, refuse_ready=False)
    cls.start_probe(app)
    assert app.probe.started and app.probe.args == (app, CHALLENGE, ORDER_ID)
    app = _app(None, refuse_ready=False)
    cls.start_probe(app)
    assert app.probe is None
