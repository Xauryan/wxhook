import sqlite3

from backend.app.repository import WeChatRepository


def sample(rows):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE messages(message_key,sort_seq,create_time,decoded,category,content,title,description,summary,reference_content)')
    db.executemany('INSERT INTO messages VALUES (?,?,?,1,\'text\',?,NULL,NULL,NULL,NULL)', rows)
    result = WeChatRepository.__new__(WeChatRepository)._media_keyword_sample(db)
    assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == len(rows)
    db.close()
    return result


def test_keywords_use_recent_messages_without_loading_the_full_archive():
    texts, basis = sample([(str(i), i, i, 'old-only' if i == 0 else 'recent-only') for i in range(20001)])
    assert len(texts) == 20000
    assert 'old-only' not in texts
    assert basis['sampled'] and basis['order'] == 'newest_first'


def test_large_documents_cannot_exceed_the_keyword_character_budget():
    texts, basis = sample([(str(i), i, i, 'x' * 100000) for i in range(501)])
    assert len(texts) == 500
    assert max(map(len, texts)) == 4000
    assert sum(map(len, texts)) == basis['characters'] == 2000000
