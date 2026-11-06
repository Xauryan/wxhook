# Verification

Date: 2026-09-22
Change: improve-wechat-rich-messages
Environment: existing local Windows API and Compose web service, http://127.0.0.1:5174/chats

## Delivered

- Type-1 Tencent news and appmsg/mmreader are parsed into ordered, deduplicated article cards and readable session summaries. Historical presentation schema is now 5.
- Image bubbles open an original-first, zoomable viewer. Failed originals fall back with an explicit quality label. Media resolver tries another image when the preferred resource is missing or invalid.
- Voice bubbles play/pause audio; video bubbles open a dismissible player; file cards open a viewer with a separate original-file download.
- Safe image, PDF and UTF-8 text previews are supported. XLSX, DOCX and PPTX have bounded, read-only content previews without macro or formula execution. XML input and generated preview output have size limits.
- 105 classic WeChat emoticon GIFs are bundled and validated. Known modern expressions use Unicode glyphs; unknown tokens remain literal. Quotes and ordinary text preserve their stored content.

## Automated Checks

- `python -m pytest backend/tests/test_rich_messages.py backend/tests/test_chat_history.py backend/tests/test_live_chat.py -q`: 85 passed.
- Chromium browser regression across rich-messages, chat-runtime, chat-layout and message-flow: 35 passed. Final duplicate-digest presentation adjustment: rich-messages 10/10 passed again.
- TypeScript project build and Vite production build passed.
- ESLint on affected chat TypeScript files and Vite configuration passed.
- OpenSpec strict validation passed.
- All 105 emoticon GIF files passed Pillow format validation.

## Real Data And Browser Acceptance

- No test chat messages were sent. Source WeChat databases were not modified. Existing dirty-worktree changes, account configuration and chat ledger were retained.
- The local web container was rebuilt and is healthy. The Windows API was restarted with the same verified account, bridge and receiver connected. Final sampled history count was 4,293 with no history error.
- Initially inspected 12 real Tencent text envelopes: all became 4-5 article cards without XML bodies. After schema refresh, 23 stored messages across supported public-message forms are categorized as public_articles.
- Browser inspection of the latest Tencent card showed 4 article links and 4 successfully loaded covers; lead image natural width was 752 pixels and following covers were 240 pixels.
- In the real conversation, the bracket-coded classic expression rendered as an image. Clicking a thumbnail opened an actual loaded original of 1,992 x 873 pixels, with the status indicating original quality. Closing the viewer returned to chat.
- A real XLSX card opened the in-page content viewer. It rendered 1 table, 200 rows and 2,200 cells, with a read-only preview note. API and UI both indicated the bounded preview rather than claiming to show the entire workbook.
- Real local voice and video resource descriptions returned available. Actual playback interactions were verified with isolated silent WAV/MP4 browser fixtures, not by listening to private audio.

## Remaining Resource Boundaries

- Missing or not-downloaded attachments are not fetched from WeChat automatically.
- One sampled encrypted dynamic sticker cache still reports decrypt_failed. The UI reports that state honestly. This is separate from the classic bracket-coded emoticon fix and requires a later cache-decryption change.
- Modern Unicode fallback glyphs are not claimed to be identical to WeChat's proprietary modern artwork.
- XLS/APK and other unsupported formats use the download fallback. Office content previews do not reproduce all formatting, images or calculated values; limits are 10 sections, 200 rows per section, 30 columns, and a bounded text output.
- External article covers can expire or fail; failed covers do not suppress article titles or valid links.
- This was local delivery only. No Git commit, push, remote deployment or OpenSpec archive was performed.
