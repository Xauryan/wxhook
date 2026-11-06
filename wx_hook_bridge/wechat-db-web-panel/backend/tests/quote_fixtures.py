from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ACCEPTED = {'accepted': 1, 'messageId': None, 'stage': 'bridge-accepted'}


def fake_quote_source(service):
    async def resolve(case_id, room):
        return {'account': 'wxid_fixture', 'room': room, 'database': 'fixture',
                'quotedMsgId': '9007199254740993', 'quotedLocalId': 17, 'createTime': 1789975235,
                'quotedFrom': 'wxid_fixture', 'quotedType': 1, 'quotedContent': 'fixture source',
                'quotedChat': room, 'baseline': {'fixture': 20}}
    service.preflight_quote_source = AsyncMock()
    service.resolve_feedback_reference = AsyncMock(side_effect=resolve)
    service.quote_source = SimpleNamespace(verify=lambda *args: {'local_type': 3, 'server_id': '9007199254740995'})
    service.bridge._post.return_value = ACCEPTED


def fake_call_quote_source(service):
    service.call_quotes.settle_delay = 0
    def resolve(account, room, primary):
        return {'account': account, 'room': room, 'database': 'fixture',
                'quotedMsgId': '9007199254740993', 'quotedLocalId': 17, 'createTime': 1789975235,
                'quotedFrom': account, 'quotedType': 1, 'quotedContent': primary['message'],
                'quotedChat': room, 'baseline': {'fixture': 20}}
    service.call_quotes.source = SimpleNamespace(preflight=Mock(), resolve=Mock(side_effect=resolve),
                                                verify=Mock(return_value={'local_type': 3, 'server_id': '9007199254740995'}))
    service.call_quotes.account = AsyncMock(return_value='wxid_fixture')
    service.bridge._post.return_value = ACCEPTED
