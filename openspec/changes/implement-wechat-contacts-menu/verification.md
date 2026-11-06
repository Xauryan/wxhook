# Verification

Date: 2026-09-22
Change: implement-wechat-contacts-menu
Local application: http://127.0.0.1:5174/chats

## Delivered

- The contacts navigation now opens a separate address book rather than filtering private chat sessions.
- Current-account contacts have real search, pinyin/alphabet groups, count, local avatars, selection, detail and management views. Contacts without messages are included.
- Details contain verified nickname, remark, alias/WeChat ID, available gender/location/signature/source/date and common groups. Unknown fields remain explicitly unavailable.
- The source-derived directory includes the current user and the built-in WeChat team contact. File helper, payment/public-account service records, groups and unrelated group members do not inflate the contact list.
- Contact and common-group buttons navigate to the existing conversation implementation without sending a message.
- Remark editing uses expected-value checks and account-scoped request receipts. The bridge response alone is not success: a fresh address-book read must contain the requested value. Uncertain requests reuse their ID for verification rather than dispatching again.
- New friends uses the current account's verified general.db snapshot, presents local request history and hides acceptance tickets. It does not automatically accept requests.
- Narrow layouts provide an explicit return-to-directory action. Account changes clear old directory query state and selections.

## Checks

- Backend: 127 tests passed across test_contacts, test_active_roster, test_live_chat, test_chat_history and test_rich_messages.
- Chromium browser: 46 tests passed across contacts-workspace, chat-layout, chat-runtime, rich-messages and message-flow.
- TypeScript and Vite production build passed.
- ESLint on affected TypeScript files passed.
- OpenSpec strict validation passed.
- The first directory sync no longer waits for an otherwise fresh legacy roster cache; this upgrade case has a regression test.

## Real Local Acceptance

- Existing Compose web service rebuilt and healthy; existing Windows API restarted with the same verified account. Bridge and receive connection remained available after startup.
- Final directory API and browser both showed 21 source-derived entries, including 1 current-user entry and 1 built-in WeChat team entry. This is computed from current data, not a hardcoded screenshot count.
- The initial 20-entry result was corrected after identifying that the WeChat team contact had been excluded together with service sessions. It remains read-only for remarks.
- Abby's detail showed the real local avatar, Guangdong/Zhaoqing location, signature, group-add source, 2026-09-14 added date and 2 common groups. These values were checked against the verified source snapshot and the live browser.
- The management table, remark dialog and cancel path were exercised on the actual page. No save was submitted.
- The new-friends view displayed 8 local records and no fake accept button.
- Clicking 发消息 opened Abby's actual conversation and composer. No chat message was sent and no draft was edited.
- A deliberately wrong-account remark request returned HTTP 409 before dispatch. Actual contact_remark_requests count remained 0.
- Historical presentation schema is 6, with general/general.db present in the verified history manifest and no reported history error.

## Boundaries

- Actual remark writes were tested only with isolated bridge/address-book fixtures. No real contact remark was changed during implementation or acceptance.
- Voice/video calls, full Moments browsing, Finder account/profile browsing and accepting/deleting friends are not implemented in this change; the UI labels or disables them instead of pretending they work.
- New-friends records are local history, not a claim that every historical request is currently actionable. At most the latest 100 local incoming request rows are shown.
- Only validated extra-buffer fields are mapped. Unsupported source codes remain visible as codes, not guessed labels; missing dates or profiles are not inferred from message history.
- Existing dirty worktree, prior rich-message changes, local chat history, drafts and send receipts were retained. No Git commit/push or remote deployment was performed. The OpenSpec change remains unarchived.
