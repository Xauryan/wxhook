## ADDED Requirements

### Requirement: Verified favorites workspace
The favorites menu SHALL open an independent workspace containing only favorites read from the verified active WeChat account.

#### Scenario: Open favorites
- **WHEN** the user selects 收藏
- **THEN** the workspace displays current-account favorite cards ordered by source update time, with source attribution and real counts
- **THEN** an unready snapshot is shown as loading/unavailable rather than an invented empty collection

### Requirement: Search categories and tags
The workspace SHALL support text search, all-favorites and image/video filtering, and source-derived tags without creating artificial tags.

#### Scenario: Filter available data
- **WHEN** the user searches or selects a category/tag
- **THEN** only matching current-account favorites are returned, with pagination and source-consistent filter counts

#### Scenario: Empty or limited tag source
- **WHEN** no tags exist or a binding operation is not understood
- **THEN** the UI distinguishes no tags from limited tag interpretation and does not invent associations

### Requirement: Safe original media preview
Favorite cards SHALL load available previews and open original-first image viewers, playable media or safe file/text/link viewers on activation.

#### Scenario: Encrypted favorite picture
- **WHEN** a local encrypted picture can be decoded and its digest matches the favorite metadata
- **THEN** the browser receives that verified resource and clicking the card opens the original where available

#### Scenario: Resource unavailable or incorrect
- **WHEN** a resource is missing, outside allowed roots, ambiguous, or fails content verification
- **THEN** no incorrect resource is served and a truthful state is shown, with a bounded retry action

#### Scenario: Account or resource identity mismatch
- **WHEN** a media request uses another account or a stale resource token
- **THEN** the request is rejected instead of serving another account or another favorite's bytes

### Requirement: Read-only source and truthful capabilities
The implementation MUST keep WeChat source data read-only and SHALL NOT claim unimplemented writes or unknown cloud capacity.

#### Scenario: Create-note capability absent
- **WHEN** the installed bridge lacks verified favorite writing support
- **THEN** new-note/tag-edit/delete controls are unavailable with an explanation and no fake favorite or local draft is presented as synchronized

#### Scenario: Capacity display
- **WHEN** only local attachment sizes are known
- **THEN** the UI identifies the displayed value as local/recorded attachment size and does not invent remaining cloud quota

### Requirement: Responsive account isolation
The favorites UI SHALL remain keyboard accessible and responsive, and MUST isolate query state by current account.

#### Scenario: Switch account during loading
- **WHEN** a prior-account response completes after switching accounts
- **THEN** it cannot populate the new account's collection or preview

#### Scenario: Narrow viewport
- **WHEN** favorites is opened on a mobile-width viewport
- **THEN** category navigation, cards, preview and return controls remain usable without horizontal page overflow
