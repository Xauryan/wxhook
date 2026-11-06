from __future__ import annotations

import html
from typing import Any


def build_text_quote_appmsg(
    reply_text: str,
    quoted: dict[str, Any],
    *,
    self_wxid: str = "",
    target_wxid: str = "",
) -> str:
    """Build the appmsg XML used by WeChat quote replies for a text message."""
    reply_text = str(reply_text or "")
    self_wxid = self_wxid or _self_wxid(quoted)
    return (
        f'<appmsg appid="" sdkver="0">'
        f"<title>{_e(reply_text)}</title>"
        "<des></des><action></action><type>57</type><showtype>0</showtype>"
        "<soundtype>0</soundtype><mediatagname></mediatagname>"
        "<messageext></messageext><messageaction></messageaction>"
        "<content></content><contentattr>0</contentattr><url></url><lowurl></lowurl>"
        "<dataurl></dataurl><lowdataurl></lowdataurl><songalbumurl></songalbumurl>"
        "<songlyric></songlyric><template_id></template_id>"
        "<appattach><totallen>0</totallen><attachid></attachid>"
        "<emoticonmd5></emoticonmd5><fileext></fileext><aeskey></aeskey></appattach>"
        "<extinfo></extinfo><sourceusername></sourceusername><sourcedisplayname></sourcedisplayname>"
        "<thumburl></thumburl><md5></md5><statextstr></statextstr>"
        f"{build_text_refermsg(quoted, target_wxid=target_wxid)}"
        f"</appmsg><fromusername>{_e(self_wxid)}</fromusername>"
    )


def build_text_refermsg(quoted: dict[str, Any], *, target_wxid: str = "") -> str:
    msg_id = _message_id(quoted)
    fromusr = _refer_fromusr(quoted)
    chatusr = _refer_chatusr(quoted, target_wxid=target_wxid)
    display = _sender_display_name(quoted)
    content = _quoted_text_content(quoted)
    timestamp = _timestamp_seconds(quoted)
    msgsource = _signature_xml(quoted)
    msg_type = _message_type(quoted)
    return (
        "<refermsg>"
        f"<content>{_e(content)}</content>"
        f"<createtime>{_e(timestamp)}</createtime>"
        f"<displayname>{_e(display)}</displayname>"
        f"<fromusr>{_e(fromusr)}</fromusr>"
        f"<svrid>{_e(msg_id)}</svrid>"
        f"<msgsource>{_e(msgsource)}</msgsource>"
        f"<type>{_e(msg_type)}</type>"
        f"<chatusr>{_e(chatusr)}</chatusr>"
        "</refermsg>"
    )


def build_wrapped_text_quote_msg(
    reply_text: str,
    quoted: dict[str, Any],
    *,
    self_wxid: str = "",
    target_wxid: str = "",
) -> str:
    """Build full <msg> wrapper variant for parsers/command paths that need it."""
    appmsg = build_text_quote_appmsg(reply_text, quoted, self_wxid=self_wxid, target_wxid=target_wxid)
    return f'<?xml version="1.0"?><msg>{appmsg}</msg>'


def _message_id(message: dict[str, Any]) -> str:
    return str(message.get("msg_id") or message.get("msgId") or message.get("id") or "")


def _timestamp_seconds(message: dict[str, Any]) -> str:
    value = str(message.get("timestamp") or message.get("time") or "")
    if not value:
        raw = _raw(message)
        value = str(raw.get("timestamp") or raw.get("time") or "")
    try:
        ts = int(value)
    except ValueError:
        return value
    if ts > 10_000_000_000:
        ts //= 1000
    return str(ts)


def _quoted_text_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("xml") or content.get("plain") or content.get("text") or content.get("summary") or "")
    return str(content or message.get("content_summary") or "")


def _message_type(message: dict[str, Any]) -> str:
    raw = _raw(message)
    value = message.get("type") or raw.get("type") or ""
    if str(value):
        return str(value)
    type_name = str(message.get("type_name") or raw.get("type_name") or "")
    return {"text": "1", "app": "49"}.get(type_name, "1")


def _sender_wxid(message: dict[str, Any]) -> str:
    sender = message.get("sender")
    if isinstance(sender, dict) and sender.get("wxid"):
        return str(sender.get("wxid") or "")
    raw = _raw(message)
    return str(message.get("sender_wxid") or raw.get("senderWxId") or raw.get("from") or raw.get("rawFrom") or "")


def _chat_wxid(message: dict[str, Any]) -> str:
    room = message.get("room")
    if isinstance(room, dict) and room.get("room_id"):
        return str(room.get("room_id") or "")
    peer = message.get("peer")
    if isinstance(peer, dict) and peer.get("wxid"):
        return str(peer.get("wxid") or "")
    raw = _raw(message)
    return str(message.get("peer_wxid") or raw.get("peerWxId") or raw.get("roomId") or raw.get("from") or raw.get("rawFrom") or "")


def _refer_fromusr(message: dict[str, Any]) -> str:
    """Original sender of the quoted message."""
    return _sender_wxid(message) or _chat_wxid(message)


def _refer_chatusr(message: dict[str, Any], *, target_wxid: str = "") -> str:
    """Conversation where the quote reply is sent."""
    return str(target_wxid or "") or _chat_wxid(message) or _sender_wxid(message)


def _sender_display_name(message: dict[str, Any]) -> str:
    sender = message.get("sender")
    if isinstance(sender, dict):
        return str(sender.get("remark") or sender.get("nickname") or sender.get("name") or sender.get("wxid") or "")
    return _sender_wxid(message)


def _self_wxid(message: dict[str, Any]) -> str:
    raw = _raw(message)
    return str(message.get("self_wxid") or raw.get("selfWxId") or raw.get("fromWxId") or raw.get("rawTo") or "")


def _signature_xml(message: dict[str, Any]) -> str:
    source = message.get("source")
    if isinstance(source, dict) and source.get("signature"):
        return str(source.get("signature") or "")
    raw = _raw(message)
    return str(raw.get("signature") or raw.get("msgsource") or "")


def _raw(message: dict[str, Any]) -> dict[str, Any]:
    source = message.get("source")
    if isinstance(source, dict) and isinstance(source.get("raw_json"), dict):
        return source["raw_json"]
    return message


def _e(value: Any) -> str:
    escaped = html.escape(str(value or ""), quote=True)
    return escaped.replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
