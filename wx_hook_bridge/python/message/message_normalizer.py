from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _strip_group_prefix(content: str) -> str:
    if ":\n" in content[:200]:
        sender, rest = content.split(":\n", 1)
        stripped = rest.lstrip()
        if stripped.startswith("<") or stripped.startswith("<?xml") or sender.startswith("wxid_"):
            return rest
    return content


def _xml_root(content: str) -> Optional[ET.Element]:
    content = _strip_group_prefix(content).strip()
    if not content.startswith("<"):
        return None
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        try:
            return ET.fromstring(html.unescape(content))
        except Exception:
            return None


def extract_at_user_list(signature: str) -> list[str]:
    text = str(signature or "").strip()
    if not text:
        return []
    root = _xml_root(text)
    if root is not None:
        raw = root.findtext(".//atuserlist") or ""
    else:
        raw = regex_tag_text(text, "atuserlist")
    if not raw:
        return []
    values = []
    for item in str(raw).replace(";", ",").split(","):
        candidate = item.strip()
        if candidate:
            values.append(candidate)
    return values


def detect_is_at_me(signature: str, self_wxid: str, self_aliases: Optional[list[str]] = None) -> bool:
    mentioned = set(extract_at_user_list(signature))
    if not mentioned:
        return False
    candidates = {str(self_wxid or "").strip()}
    for alias in self_aliases or []:
        value = str(alias or "").strip()
        if value:
            candidates.add(value)
    candidates.discard("")
    return bool(candidates & mentioned)


def message_type_name(value: Any) -> str:
    type_s = str(value or "")
    return {
        "1": "text",
        "3": "image",
        "34": "voice",
        "37": "friend_request",
        "42": "contact_card",
        "43": "video",
        "47": "emoji",
        "48": "location",
        "49": "app",
        "50": "voip",
        "51": "status",
        "10000": "system",
    }.get(type_s, f"type_{type_s}" if type_s else "unknown")


def type_label_zh(type_name: str) -> str:
    return {
        "text": "文本",
        "image": "图片",
        "voice": "语音",
        "friend_request": "好友请求",
        "contact_card": "名片",
        "video": "视频",
        "emoji": "表情",
        "location": "位置",
        "app": "应用消息",
        "voip": "通话",
        "status": "状态",
        "system": "系统",
    }.get(type_name, type_name)


def timestamp_to_datetime(value: Any) -> str:
    try:
        ts = int(str(value or "0"))
        if ts > 10_000_000_000:
            ts = ts // 1000
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def contact_profile(wxid: str, contacts: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    wxid = str(wxid or "")
    item = contacts.get(wxid, {}) if wxid else {}
    nickname = str(item.get("nickname") or "")
    remark = str(item.get("remark") or "")
    if wxid == "filehelper" and not nickname:
        nickname = "文件传输助手"
    return {
        "wxid": wxid,
        "nickname": nickname,
        "remark": remark,
        "account": str(item.get("account") or ""),
        "avatar": str(item.get("head_image") or item.get("avatar") or ""),
    }


def display_contact_name(profile: Dict[str, Any]) -> str:
    return str(profile.get("remark") or profile.get("nickname") or profile.get("wxid") or "")


def strip_group_sender(content: str) -> Tuple[str, str]:
    if ":\n" in content[:200]:
        sender, rest = content.split(":\n", 1)
        sender = sender.strip()
        stripped = rest.lstrip()
        if stripped.startswith("<") or stripped.startswith("<?xml"):
            return sender, rest
        # WeChat group text can be prefixed by wxid_xxx, alias ids like s12345,
        # or other username forms before ":\n". Treat compact ids as sender ids.
        if re.match(r"^[A-Za-z0-9_@.\-]{2,128}$", sender):
            return sender, rest
    return "", content


def plain_summary(content: str, type_name: str) -> Tuple[str, str, str]:
    text = str(content or "")
    sender_prefix, stripped = strip_group_sender(text)
    del sender_prefix
    candidate = stripped.strip()
    xml = candidate if candidate.startswith("<") else ""
    if xml:
        summary = type_label_zh(type_name)
        try:
            root = _xml_root(candidate)
            if root is not None:
                if type_name == "emoji":
                    emoji = next(root.iter("emoji"), None)
                    if emoji is not None:
                        summary = "[表情]"
                elif type_name == "app":
                    title = root.findtext(".//title") or root.findtext(".//des") or ""
                    summary = title.strip() or "[应用消息]"
                elif type_name == "video":
                    summary = "[视频]"
                elif type_name == "image":
                    summary = "[图片]"
        except Exception:
            pass
        return candidate, summary, xml
    summary = " ".join(candidate.split())
    if not summary:
        summary = f"[{type_label_zh(type_name)}]"
    return candidate, summary[:240], ""


def compact_display_text(value: str, limit: int = 500) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text[:limit]


def direct_child_text(root: ET.Element, path: str) -> str:
    found = root.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def regex_tag_text(text: str, tag: str, start: int = 0) -> str:
    pattern = rf"<{re.escape(tag)}(?:\s[^>]*)?>(.*?)</{re.escape(tag)}>"
    match = re.search(pattern, text[start:], re.S)
    return html.unescape(match.group(1)).strip() if match else ""


APPMSG_NATIVE_CARD_SUBTYPES = frozenset({3, 5})


def extract_appmsg_subtype(content: str) -> str:
    text = _strip_group_prefix(str(content or "")).strip()
    if not text:
        return ""
    root = _xml_root(text)
    if root is not None:
        value = direct_child_text(root, "./appmsg/type")
        if value:
            return value
    appmsg = re.search(r"<appmsg\b[^>]*>(.*?)</appmsg>", text, re.S)
    if appmsg:
        return regex_tag_text(appmsg.group(1), "type")
    appmsg_start = text.find("<appmsg")
    return regex_tag_text(text, "type", appmsg_start) if appmsg_start >= 0 else ""


def is_native_card_appmsg_subtype(value: Any) -> bool:
    try:
        subtype = int(str(value or "").strip())
    except ValueError:
        return False
    return subtype in APPMSG_NATIVE_CARD_SUBTYPES


def parse_appmsg_card(content: str) -> dict[str, str]:
    """Extract structured card fields from a source ``<appmsg>`` payload."""
    card: dict[str, str] = {
        "title": "",
        "des": "",
        "type": "0",
        "action": "view",
        "url": "",
        "dataurl": "",
        "songalbumurl": "",
        "thumburl": "",
        "appid": "",
        "sourceusername": "",
        "sourcedisplayname": "",
    }
    text = str(content or "")
    idx = text.find("<msg")
    if idx < 0:
        idx = text.find("<appmsg")
    if idx < 0:
        idx = text.find("<")
    if idx > 0:
        text = text[idx:]
    try:
        root = ET.fromstring(text)
        appmsg = root if root.tag == "appmsg" else root.find(".//appmsg")
        if appmsg is None:
            appmsg = root

        def child_text(tag: str) -> str:
            found = appmsg.find(tag)
            return (found.text or "").strip() if found is not None and found.text else ""

        card["appid"] = (appmsg.get("appid") or "").strip()
        card["title"] = child_text("title")
        card["des"] = child_text("des")
        card["type"] = child_text("type") or "0"
        card["action"] = child_text("action") or "view"
        card["url"] = child_text("url")
        card["dataurl"] = child_text("dataurl")
        card["songalbumurl"] = child_text("songalbumurl")
        card["thumburl"] = child_text("thumburl")
        card["sourceusername"] = child_text("sourceusername")
        card["sourcedisplayname"] = child_text("sourcedisplayname")
    except Exception as exc:
        card["_parse_error"] = str(exc)
    return card


def summarize_embedded_message(content: str, fallback: str = "") -> str:
    text = html.unescape(str(content or "")).strip()
    if not text:
        return fallback
    title = regex_tag_text(text, "title")
    if title:
        return compact_display_text(title)
    root = _xml_root(text)
    if root is not None:
        app_title = direct_child_text(root, "./appmsg/title")
        app_type = direct_child_text(root, "./appmsg/type")
        if app_title:
            return compact_display_text(app_title)
        if root.find(".//img") is not None:
            return "[图片]"
        if root.find(".//emoji") is not None:
            return "[表情]"
        if app_type:
            return f"[应用消息 type={app_type}]"
    return compact_display_text(text) or fallback


def extract_quote_from_truncated_xml(content: str) -> Dict[str, Any]:
    text = str(content or "")
    refer_start = text.find("<refermsg>")
    if refer_start < 0:
        return {}
    reply_text = summarize_embedded_message(regex_tag_text(text, "title"), "[回复]")
    refer_text = text[refer_start:]
    raw_refer_content = ""
    content_start = refer_text.find("<content>")
    if content_start >= 0:
        content_body_start = content_start + len("<content>")
        content_end = refer_text.find("</content>", content_body_start)
        raw_refer_content = refer_text[content_body_start:] if content_end < 0 else refer_text[content_body_start:content_end]
    refer_content = summarize_embedded_message(raw_refer_content, "[被引用消息]")
    return {
        "kind": "quote",
        "app_type": regex_tag_text(text, "type"),
        "reply_text": reply_text,
        "refer": {
            "msg_id": regex_tag_text(refer_text, "svrid"),
            "type": regex_tag_text(refer_text, "type"),
            "from_wxid": regex_tag_text(refer_text, "chatusr"),
            "sender_wxid": regex_tag_text(refer_text, "fromusr"),
            "sender_name": regex_tag_text(refer_text, "displayname") or regex_tag_text(refer_text, "fromusr"),
            "content": refer_content,
            "raw_content": html.unescape(raw_refer_content),
        },
    }


def extract_quote_from_xml_content(content: str) -> Dict[str, Any]:
    root = _xml_root(str(content or ""))
    if root is None:
        return extract_quote_from_truncated_xml(content)
    appmsg = root.find("./appmsg")
    if appmsg is None:
        return {}
    app_type = direct_child_text(root, "./appmsg/type")
    refer = root.find("./appmsg/refermsg")
    if refer is None or len(list(refer)) == 0:
        return extract_quote_from_truncated_xml(content)

    reply_text = summarize_embedded_message(direct_child_text(root, "./appmsg/title"), "[回复]")
    raw_refer_content = direct_child_text(refer, "content")
    refer_content = summarize_embedded_message(raw_refer_content, "[被引用消息]")
    displayname = direct_child_text(refer, "displayname")
    fromusr = direct_child_text(refer, "fromusr")
    chatusr = direct_child_text(refer, "chatusr")
    refer_type = direct_child_text(refer, "type")
    refer_msg_id = direct_child_text(refer, "svrid")

    return {
        "kind": "quote",
        "app_type": app_type,
        "reply_text": reply_text,
        "refer": {
            "msg_id": refer_msg_id,
            "type": refer_type,
            "from_wxid": chatusr,
            "sender_wxid": fromusr,
            "sender_name": displayname or fromusr,
            "content": refer_content,
            "raw_content": html.unescape(raw_refer_content),
        },
    }


def extract_media_from_xml_content(content: str, msg_id: str = "", msg_type: Any = "") -> Dict[str, Any]:
    root = _xml_root(str(content or ""))
    media: Dict[str, Any] = {
        "msgId": msg_id,
        "type": msg_type,
        "items": [],
    }
    if root is None:
        media["ok"] = False
        media["error"] = "no_xml_content"
        return media

    def add(kind: str, url: str, **extra: Any) -> None:
        if not url:
            return
        item = {"kind": kind, "url": html.unescape(url)}
        item["preview"] = str(item["url"]).startswith(("http://", "https://"))
        item.update({k: v for k, v in extra.items() if v})
        media["items"].append(item)

    for emoji in root.iter("emoji"):
        add("emoji_thumb", emoji.attrib.get("thumburl", ""), aeskey=emoji.attrib.get("aeskey", ""), md5=emoji.attrib.get("md5", ""))
        add("emoji_cdn", emoji.attrib.get("cdnurl", ""), aeskey=emoji.attrib.get("aeskey", ""), md5=emoji.attrib.get("md5", ""))
        add("emoji_encrypt", emoji.attrib.get("encrypturl", ""), aeskey=emoji.attrib.get("aeskey", ""), md5=emoji.attrib.get("md5", ""))
        add("emoji_extern", emoji.attrib.get("externurl", ""), aeskey=emoji.attrib.get("aeskey", ""), md5=emoji.attrib.get("externmd5", ""))

    for video in root.iter("videomsg"):
        add("video_thumb", video.attrib.get("cdnthumburl", ""), aeskey=video.attrib.get("cdnthumbaeskey", ""), md5=video.attrib.get("md5", ""))
        add("video_cdn", video.attrib.get("cdnvideourl", ""), aeskey=video.attrib.get("aeskey", ""), md5=video.attrib.get("md5", ""))
        add("video_raw", video.attrib.get("cdnrawvideourl", ""), aeskey=video.attrib.get("cdnrawvideoaeskey", ""), md5=video.attrib.get("rawmd5", ""))

    for img in root.iter("img"):
        add("image_thumb", img.attrib.get("cdnthumburl", ""), aeskey=img.attrib.get("cdnthumbaeskey", ""), md5=img.attrib.get("md5", ""))
        add("image_cdn", img.attrib.get("cdnmidimgurl", "") or img.attrib.get("cdnbigimgurl", ""), aeskey=img.attrib.get("aeskey", ""), md5=img.attrib.get("md5", ""))
        if img.attrib.get("cdnbigimgurl"):
            add("image_hd", img.attrib.get("cdnbigimgurl", ""), aeskey=img.attrib.get("aeskey", ""), md5=img.attrib.get("md5", ""))

    for tag in root.iter():
        if tag.tag in ("cdnthumburl", "thumburl", "url", "dataurl", "lowurl", "lowdataurl"):
            text = (tag.text or "").strip()
            if text.startswith("http"):
                add(tag.tag, text)
        if tag.tag in ("cdnthumbaeskey", "aeskey", "attachid", "fileuploadtoken", "fileext", "totallen"):
            media.setdefault("fields", {})[tag.tag] = (tag.text or "").strip()

    media["ok"] = bool(media["items"])
    if not media["ok"]:
        media["error"] = "no_direct_url"
    return media


def normalize_message(raw: Dict[str, Any], contacts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    contacts = contacts or {}
    msg_id = str(raw.get("msgId") or raw.get("msg_id") or "")
    raw_from = str(raw.get("rawFrom") or raw.get("from") or "")
    raw_to = str(raw.get("rawTo") or raw.get("fromWxId") or "")
    self_wxid = str(raw.get("selfWxId") or "")
    self_aliases = [
        str(raw.get("selfDisplayWxId") or ""),
        str(raw.get("selfAtWxId") or ""),
        str(raw.get("botWxId") or ""),
    ]
    content_raw = str(raw.get("content") or "")
    sender_prefix, content_without_prefix = strip_group_sender(content_raw)
    type_raw = str(raw.get("type") or "")
    type_name = message_type_name(type_raw)
    timestamp = str(raw.get("timestamp") or raw.get("time") or "")
    room_id = str(raw.get("roomId") or "")
    if not room_id and "@chatroom" in raw_from:
        room_id = raw_from
    if not room_id and "@chatroom" in raw_to:
        room_id = raw_to
    is_group = bool(room_id)
    scene = "group" if is_group else "filehelper" if "filehelper" in (raw_from, raw_to) else "direct"
    direction = str(raw.get("direction") or "")
    if not direction:
        if bool(raw.get("isSendMsg")):
            direction = "outgoing"
        elif is_group and self_wxid and raw_to == self_wxid and str(raw.get("fromWxId") or "") == self_wxid and not bool(raw.get("isIncoming")):
            # Group echoes from our own account often arrive without isSendMsg=true.
            direction = "outgoing"
        elif bool(raw.get("isIncoming")):
            direction = "incoming"
        elif raw_to == "filehelper" and raw_from.startswith("wxid_"):
            direction = "outgoing"
        elif raw_from.startswith("wxid_") and raw_to and not raw_to.startswith("wxid_") and "@chatroom" not in raw_to:
            direction = "outgoing"
        else:
            direction = "unknown"

    sender_wxid = str(raw.get("senderWxId") or "")
    if direction == "outgoing" and self_wxid:
        sender_wxid = self_wxid
    elif is_group and sender_prefix:
        sender_wxid = sender_prefix
    if not sender_wxid:
        sender_wxid = raw_from
    peer_wxid = str(raw.get("peerWxId") or "")
    if not peer_wxid:
        peer_wxid = room_id if is_group else (raw_to if direction == "outgoing" else raw_from)
    if not peer_wxid:
        peer_wxid = raw_from or raw_to
    if not sender_wxid:
        sender_wxid = raw_from or peer_wxid or raw_to

    from_profile = contact_profile(raw_from, contacts)
    to_profile = contact_profile(raw_to, contacts)
    peer_profile = contact_profile(peer_wxid, contacts)
    sender_profile = contact_profile(sender_wxid, contacts)
    room_profile = contact_profile(room_id, contacts)
    content_text, summary, xml = plain_summary(content_raw, type_name)
    if is_group and sender_prefix:
        content_text = content_without_prefix

    media = extract_media_from_xml_content(xml, msg_id=msg_id, msg_type=type_raw or type_name) if xml else {"items": []}
    quote = extract_quote_from_xml_content(xml) if xml else {}
    signature = str(raw.get("signature") or raw.get("msgsource") or "")
    is_at_me = bool(is_group and detect_is_at_me(signature, self_wxid, self_aliases=self_aliases))
    if quote:
        summary = f"{quote.get('reply_text') or '[回复]'} ｜引用 {quote.get('refer', {}).get('sender_name') or '-'}：{quote.get('refer', {}).get('content') or ''}"[:240]
    subtype = str(raw.get("subType") or raw.get("subtype") or raw.get("appmsgType") or "")
    if not subtype and type_name == "app":
        subtype = extract_appmsg_subtype(content_without_prefix or content_raw)

    return {
        "id": msg_id,
        "msg_id": msg_id,
        "local_id": str(raw.get("localId") or raw.get("localid") or raw.get("msgLocalId") or raw.get("localMsgId") or ""),
        "type": int(type_raw) if type_raw.isdigit() else type_raw,
        "subtype": subtype,
        "type_name": type_name,
        "type_label": type_label_zh(type_name),
        "scene": scene,
        "direction": direction,
        "is_self": bool(direction == "outgoing" or (self_wxid and sender_wxid == self_wxid)),
        "is_group": is_group,
        "is_at_me": is_at_me,
        "is_system": type_name == "system",
        "timestamp": timestamp,
        "datetime": timestamp_to_datetime(timestamp),
        "from": from_profile,
        "to": to_profile,
        "peer": peer_profile,
        "room": {
            "room_id": room_id,
            "name": display_contact_name(room_profile) if room_id else "",
            "member_count": None,
        },
        "sender": sender_profile,
        "self_wxid": self_wxid,
        "content": {
            "text": content_text,
            "plain": content_text,
            "xml": xml,
            "summary": summary,
        },
        "media": media,
        "quote": quote,
        "status": {
            "delivered": True,
            "revoked": False,
            "downloaded": False,
            "decoded": False,
        },
        "source": {
            "from_wxid_raw": raw_from,
            "to_wxid_raw": raw_to,
            "signature": signature,
            "at_user_list": extract_at_user_list(signature),
            "raw_json": raw,
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }



