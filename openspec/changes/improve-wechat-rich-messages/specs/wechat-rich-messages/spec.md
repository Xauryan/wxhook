## ADDED Requirements

### Requirement: Direct media bubbles
The chat UI SHALL render local images, stickers, video, voice and files as interactive media bubbles rather than raw transport text. An unavailable resource MUST have an explicit failure or missing state.

#### Scenario: Open an image with original and thumbnail
- **WHEN** a message has both a local original and a thumbnail
- **THEN** clicking its image bubble opens the original in a dismissible in-page viewer
- **THEN** failure of the original falls back to an available preview or thumbnail with an accurate quality label

#### Scenario: Play audio and video
- **WHEN** the user activates a playable voice or video bubble
- **THEN** voice plays from the bubble and video opens with player controls in the page
- **THEN** duration and playback state are visible and leaving the message stops playback

#### Scenario: Open a file
- **WHEN** the user activates a local file card
- **THEN** the page opens a preview for supported safe image, PDF or text content, or a bounded read-only content preview for XLSX, DOCX and PPTX, and provides a separate download action
- **THEN** unsupported formats display their filename, size and explicit download fallback without executing attachment content

#### Scenario: Missing preferred variant
- **WHEN** a preferred image variant cannot be resolved but another local image variant is available
- **THEN** the resolver returns the available image and preserves the unavailable variant's truthful state

### Requirement: Structured public account articles
The system SHALL parse public account and subscription multi-article messages, including type-1 Tencent news `mmreader` and `appmsg/mmreader`, into ordered article data and readable conversation summaries.

#### Scenario: Tencent news mixed article schema
- **WHEN** a message includes `topnew`, `newitem` and a duplicate compatibility `item`
- **THEN** the UI displays one first-cover article and ordered subsequent title-and-cover rows without duplicates or XML

#### Scenario: Subscription message
- **WHEN** an app message contains multiple `mmreader/category/item` entries
- **THEN** every valid article retains its title, destination, cover, digest and source where present
- **THEN** the conversation summary uses the first article title

#### Scenario: Malformed or untrusted article fields
- **WHEN** an article has a missing cover, malformed envelope or unsafe link protocol
- **THEN** the UI displays a readable fallback without executing markup or exposing raw XML
- **THEN** only valid HTTP(S) links can be opened

### Requirement: Inline WeChat emoticons
The UI SHALL display known WeChat bracket-coded emoticons as graphics in message bodies and quoted text, with accessible names, without altering stored message text.

#### Scenario: Mixed text and emoticons
- **WHEN** text contains known codes such as `[憨笑]` mixed with normal text and unknown bracket tokens
- **THEN** known codes display as named graphics and all remaining text, line breaks and literal markup remain intact

### Requirement: Safe account scoped presentation
The implementation MUST preserve active-account checks, opaque media URLs, local path containment and source database immutability.

#### Scenario: Wrong account or changed resource identity
- **WHEN** a request uses another account or a stale resource token
- **THEN** the media route rejects access instead of serving a different resource

#### Scenario: Refresh historical presentation
- **WHEN** the new parser version is applied to existing verified history
- **THEN** existing message identities and raw evidence remain, while new presentation fields and deltas become available
