import zstandard

from backend.app.parsing import (
    decode_wcdb_value,
    extract_protobuf_strings,
    is_valid_keyword_token,
    message_quality_score,
    normalize_human_text,
    parse_message_content,
    split_local_type,
)


def test_message_links_reject_unsafe_or_malformed_urls_without_losing_content() -> None:
    for url in ("javascript:alert(1)", "http://["):
        parsed = parse_message_content(
            f"<msg><appmsg><title>original title</title><type>5</type><url>{url}</url></appmsg></msg>",
            49, 5, False,
        )
        assert parsed.url is None
        assert parsed.title == "original title"


def test_wcdb_zstandard_and_64_bit_app_type() -> None:
    xml = (
        '<msg><appmsg><title>report.zip</title><type>6</type>'
        '<appattach><totallen>42</totallen></appattach></appmsg></msg>'
    )
    compressed = zstandard.ZstdCompressor().compress(xml.encode())
    decoded = decode_wcdb_value(compressed)
    parsed = parse_message_content(decoded.text, 49, 6, False)
    assert decoded.decoded and decoded.compressed
    assert parsed.category == "file"
    assert parsed.file_name == "report.zip"
    assert parsed.file_size == 42
    assert split_local_type((57 << 32) | 49) == (49, 57)


def test_group_prefix_and_quote_fields() -> None:
    content = "wxid_sender:\n" + (
        "<msg><appmsg><title>收到</title><type>57</type><refermsg>"
        "<svrid>123</svrid><fromusr>wxid_a</fromusr><chatusr>wxid_b</chatusr>"
        "<displayname>A</displayname><content>原消息</content>"
        "</refermsg></appmsg></msg>"
    )
    parsed = parse_message_content(content, 49, 57, True)
    assert parsed.sender_prefix == "wxid_sender"
    assert parsed.category == "quote"
    assert parsed.reference_server_id == 123
    assert parsed.reference_content == "原消息"


def test_quality_score_is_deterministic() -> None:
    assert message_quality_score("ok", "text") == 0
    assert message_quality_score("x" * 80 + "\nhttps://example.test", "link") == 4


def test_extracts_length_delimited_protobuf_text() -> None:
    value = "真实文件名.zip".encode("utf-8")
    payload = bytes((0x0A, len(value))) + value
    assert extract_protobuf_strings(payload) == ["真实文件名.zip"]


def test_human_text_removes_xml_transport_terms_and_preserves_visible_text() -> None:
    raw = (
        '<?xml version="1.0"?><msgsource><type>57</type>'
        '<title><![CDATA[真实项目进度]]></title>'
        '<des>&lt;span&gt;客户确认交付&lt;/span&gt;</des>'
        '<url>https://example.test/path</url></msgsource>'
    )
    cleaned = normalize_human_text(raw)
    assert "真实项目进度" in cleaned
    assert "客户确认交付" in cleaned
    assert "msgsource" not in cleaned
    assert "CDATA" not in cleaned
    assert "https" not in cleaned


def test_keyword_token_requires_chinese_or_ascii_letter() -> None:
    assert is_valid_keyword_token("客户")
    assert is_valid_keyword_token("R70")
    assert is_valid_keyword_token("5G套餐")
    assert not is_valid_keyword_token("129")
    assert not is_valid_keyword_token("...")
    assert not is_valid_keyword_token("——")
