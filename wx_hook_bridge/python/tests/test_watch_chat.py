import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from python.tools import watch_chat


VIDEO_XML = '''<?xml version="1.0"?>
<msg><videomsg aeskey="private-key" cdnvideourl="opaque-cdn-id"
length="599750" playlength="11" cdnthumbwidth="288" cdnthumbheight="180"
rawlength="1035563" /></msg>'''


class WatchChatTests(unittest.TestCase):
    def test_video_metadata_does_not_dump_transport_fields(self):
        body = watch_chat.message_body(VIDEO_XML, 43)
        self.assertEqual(body, "[视频] 时长: 11 秒 | 大小: 585.7 KiB\n缩略图: 288×180")
        self.assertNotIn("private-key", body)
        self.assertNotIn("opaque-cdn-id", body)

    def test_plain_text_and_raw_mode_remain_lossless(self):
        for content in ("  第一行\n第二行\t&amp;  ", "<msg><videomsg playlength='11'/></msg>"):
            with self.subTest(content=content):
                self.assertEqual(watch_chat.message_body(content, 1), content)
        self.assertEqual(watch_chat.message_body(VIDEO_XML, 43, raw_content=True), VIDEO_XML)

    def test_escaped_media_and_direct_root(self):
        body = watch_chat.message_body("&lt;videomsg playlength=&quot;2&quot;/&gt;", 43)
        self.assertEqual(body, "[视频] 时长: 2 秒")

    def test_other_media_types(self):
        cases = [
            (34, '<msg><voicemsg voicelength="1250" length="2048"/></msg>', "[语音] 时长: 1.25 秒 | 大小: 2.0 KiB"),
            (3, '<msg><img cdnmidimglength="1024"/></msg>', "[图片] 大小: 1.0 KiB"),
            (47, '<msg><emoji aeskey="private"/></msg>', "[表情]"),
            (42, '<msg nickname="张三" username="wxid_friend"/>', "[名片] 张三 (wxid_friend)"),
            (48, '<msg><location poiname="车站" label="车站" x="31" y="121"/></msg>', "[位置] 车站\n坐标: 31, 121"),
        ]
        for kind, content, expected in cases:
            with self.subTest(kind=kind):
                self.assertEqual(watch_chat.message_body(content, kind), expected)

    def test_app_cdata_entities_and_composite_type(self):
        link = '<msg><appmsg><type>5</type><title><![CDATA[标题 <示例>]]></title><des>介绍 &amp; 说明</des><url>https://example.test/?a=1&amp;b=2</url></appmsg></msg>'
        expected = "[链接] 标题 <示例>\n介绍 & 说明\n链接: https://example.test/?a=1&b=2"
        self.assertEqual(watch_chat.message_body(link, 49), expected)
        file = '<msg><appmsg><title>报告.pdf</title><appattach><totallen>1048576</totallen></appattach></appmsg></msg>'
        self.assertEqual(watch_chat.message_body(file, (6 << 32) | 49), "[文件] 报告.pdf\n大小: 1.0 MiB")

    def test_quote_renders_referenced_media(self):
        quote = '<msg><appmsg><type>57</type><title>看看这个</title><refermsg><type>43</type><displayname>张三</displayname><content>&lt;msg&gt;&lt;videomsg playlength="11"/&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>'
        self.assertEqual(watch_chat.message_body(quote, 49), "[引用回复] 看看这个\n引用 张三: [视频] 时长: 11 秒")

    def test_system_and_unrecognized_messages(self):
        self.assertEqual(watch_chat.message_body('<sysmsg><revokemsg><replacemsg>张三撤回了一条消息</replacemsg></revokemsg></sysmsg>', 10002), "张三撤回了一条消息")
        for content in ('<msg><newformat value="1"/></msg>', 'unstructured body'):
            self.assertEqual(watch_chat.message_body(content, 43), content)

    def test_invalid_xml_and_numbers_preserve_evidence(self):
        for content in ('<msg><videomsg', '<!DOCTYPE msg [<!ENTITY body "expanded">]><msg>&body;</msg>'):
            body = watch_chat.message_body(content, 43)
            self.assertIn("XML 解析失败", body)
            self.assertTrue(body.endswith(content))
        for number in ("nan", "inf", "-1", "bad"):
            content = f'<msg><videomsg playlength="{number}" length="{number}"/></msg>'
            self.assertEqual(watch_chat.message_body(content, 43), "[视频]")

    def test_watch_formats_persisted_events_and_stays_readonly(self):
        with tempfile.TemporaryDirectory(prefix="watch-chat-test-") as temp:
            path = Path(temp) / "events.sqlite"
            incoming = {"source": "wx_hook_bridge", "event_type": "message", "direction": "incoming",
                        "room_id": "room@chatroom", "sender_id": "wxid_sender", "self_id": "wxid_self",
                        "message_id": "4406982382020296705", "message_type": 43,
                        "content": "wxid_sender:\n" + VIDEO_XML, "content_truncated": True}
            outgoing = dict(incoming, direction="outgoing", message_id="outgoing")
            other_source = dict(incoming, source="history", message_id="historical")
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE live_messages (sequence INTEGER PRIMARY KEY, raw TEXT, account TEXT);'
                                 'CREATE TABLE live_names (account TEXT, username TEXT, name TEXT);')
                db.executemany('INSERT INTO live_messages (raw,account) VALUES (?,?)',
                               [(json.dumps(event, ensure_ascii=False), "wxid_self") for event in (incoming, outgoing, other_source)])
                db.executemany('INSERT INTO live_names VALUES (?,?,?)',
                               [("wxid_self", "room@chatroom", "测试群"), ("wxid_self", "wxid_sender", "张三")])
            original = path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(watch_chat.main(["--database", str(path), "--from-start", "--duration", "0.01", "--poll-interval", "0.005"]), 0)
            rendered = output.getvalue()
            self.assertIn("收到 · 群聊 · 视频 [Hook 正文已截断]", rendered)
            self.assertIn("测试群 (room@chatroom)", rendered)
            self.assertIn("张三 (wxid_sender)", rendered)
            self.assertIn("正文: [视频] 时长: 11 秒", rendered)
            self.assertNotIn("outgoing", rendered)
            self.assertNotIn("historical", rendered)
            self.assertNotIn("aeskey", rendered)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(watch_chat.main(["--database", str(path), "--from-start", "--include-outgoing", "--raw-content", "--duration", "0.01"]), 0)
            self.assertIn(VIDEO_XML, output.getvalue())
            self.assertIn("发出 · 群聊", output.getvalue())
            self.assertNotIn("historical", output.getvalue())
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
