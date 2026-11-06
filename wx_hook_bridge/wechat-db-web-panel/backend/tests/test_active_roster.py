from types import SimpleNamespace

import pytest

from backend.app.active_roster import ActiveWechatRoster, ActiveRosterError, contact_relation


@pytest.mark.parametrize('username,kind,deleted,in_room,flag,expected',[
    ('friend',1,0,0,3,'friend'),('stranger',3,0,0,4,None),
    ('member',3,0,0,4,None),('deleted',1,1,0,3,None),
    ('room@chatroom',2,0,1,2,'group'),('left@chatroom',2,0,0,2,None),
    ('saved@chatroom',1,0,1,3,'group'),('saved_left@chatroom',1,0,2,3,None),
    ('saved_deleted@chatroom',1,1,1,3,None),
    ('self_a',1,0,0,3,None),('filehelper',0,0,0,0,'system'),
])
def test_roster_scope(username,kind,deleted,in_room,flag,expected):
    assert contact_relation(username,kind,deleted,in_room,flag,'self_a') == expected


@pytest.mark.parametrize('process_name', ['Weixin.exe', 'Weixi1.exe', 'Weixin2.exe'])
def test_binding_uses_bridge_process_open_database_not_directory_recency(tmp_path,monkeypatch,process_name):
    import backend.app.active_roster as module
    current = tmp_path/'self_a_1234'/'db_storage'/'contact'/'contact.db'
    class Process:
        pid=101
        def name(self): return process_name
        def create_time(self): return 123.0
        def open_files(self): return [SimpleNamespace(path=str(current))]
    monkeypatch.setattr(module.psutil,'net_connections',lambda **_: [SimpleNamespace(status=module.psutil.CONN_LISTEN,laddr=SimpleNamespace(port=19088,ip='127.0.0.1'),pid=101)])
    monkeypatch.setattr(module.psutil,'Process',lambda _: Process())
    reader=ActiveWechatRoster('http://127.0.0.1:19088',tmp_path,tmp_path/'keys',tmp_path/'cache')
    assert reader.verify('self_a')['source'] == current
    with pytest.raises(ActiveRosterError): reader.verify('self_b',force=True)
    monkeypatch.setattr(Process,'open_files',lambda _: [SimpleNamespace(path=str(current)),SimpleNamespace(path=str(tmp_path/'self_b_1234'/'db_storage'/'contact'/'contact.db'))])
    with pytest.raises(ActiveRosterError): reader.verify('self_a',force=True)
