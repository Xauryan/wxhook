import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows process selection')


@pytest.mark.parametrize('target', [None, {'pid': 123, 'name': 'Weixi1.exe'}])
def test_explicit_pid_never_falls_back_to_another_account(monkeypatch, target):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'inject'))
    import wechat_native

    processes = [{'pid': 456, 'name': 'Weixin.exe', 'weixin_base': 100}]
    if target:
        processes.append(target)
    monkeypatch.setenv('WX_TARGET_PID', '123')
    monkeypatch.setattr(wechat_native, 'find_wechat_processes', lambda: processes)
    monkeypatch.setattr(wechat_native, 'find_wechat_windows', lambda *args: pytest.fail('Unexpected fallback'))
    assert wechat_native.choose_wechat_target() is None


def test_explicit_renamed_instance_is_selected(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'inject'))
    import wechat_native

    target = {'pid': 123, 'name': 'Weixi1.exe', 'weixin_base': 100}
    monkeypatch.setenv('WX_TARGET_PID', '123')
    monkeypatch.setattr(wechat_native, 'find_wechat_processes', lambda: [target])
    monkeypatch.setattr(wechat_native, 'find_wechat_windows', lambda pid: [{'pid': pid, 'tid': 7}])
    assert wechat_native.choose_wechat_target() == {**target, 'tid': 7}
