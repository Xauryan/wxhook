## ADDED Requirements

### Requirement: Independent real address book
The contact navigation SHALL open an address book independent of chat sessions, derived from the verified current WeChat contact database.

#### Scenario: Contact has no chat history
- **WHEN** a verified friend has no chat messages
- **THEN** the friend appears in the directory and can be selected without inventing a chat history

#### Scenario: Membership and grouping
- **WHEN** the address book is loaded
- **THEN** real contacts are searchable by remark, nickname, alias and pinyin, grouped alphabetically, and the count matches the directory membership
- **THEN** non-directory service identities, public accounts, groups, deleted contacts and unrelated group members are excluded, while the built-in WeChat team contact remains when marked as a valid friend in the source

### Requirement: Evidence based contact detail
The contact detail panel SHALL show available real nickname, remark, alias, avatar, gender, location, signature, source, added date and common groups, without substituting screenshot values or unverified inferences.

#### Scenario: Available and missing fields
- **WHEN** a contact is selected
- **THEN** verified fields and actual common groups are displayed and missing fields are marked unavailable
- **THEN** unsupported social feeds or calls are not presented as working actions

### Requirement: Directory to chat navigation
The directory SHALL support opening the existing chat interface for a selected send-eligible contact or current common group.

#### Scenario: Start a conversation
- **WHEN** the user clicks 发消息
- **THEN** the correct account-scoped conversation opens without sending any automatic message
- **THEN** drafts and rich-message behavior remain available

### Requirement: Verified remark management
The directory management view SHALL provide real contact rows and an edit-remark workflow that uses the bridge and confirms the stored result.

#### Scenario: Confirmed save
- **WHEN** the user saves a changed remark and the refreshed WeChat address book contains that exact value
- **THEN** the UI reports confirmed success and updates both contact views and chat labels

#### Scenario: Conflict or uncertain delivery
- **WHEN** the original remark changed, the bridge fails, times out, or the new value cannot be confirmed
- **THEN** the UI reports conflict, failure or pending confirmation instead of false success
- **THEN** repeated use of the same request ID does not dispatch the write twice

### Requirement: New friends with truthful source state
The menu SHALL provide a separate new-friends view using only available current-account local request records and SHALL NOT expose acceptance tickets or automatically accept requests.

#### Scenario: Request source unavailable
- **WHEN** no current-account request snapshot is ready
- **THEN** the view explains that limitation instead of reporting an invented empty or pending list

### Requirement: Account safety and responsive access
All directory reads and remark operations MUST enforce current-account ownership, and the UI SHALL remain accessible on desktop and mobile.

#### Scenario: Account changes during a request
- **WHEN** the active WeChat account changes
- **THEN** stale results cannot update the new account's view and old selections and queries are cleared

#### Scenario: Mobile contact detail
- **WHEN** a contact is selected on a narrow viewport
- **THEN** a readable detail page with an explicit return-to-directory action is shown without horizontal page overflow
