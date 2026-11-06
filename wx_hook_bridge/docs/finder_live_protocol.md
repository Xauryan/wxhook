# Finder Live Protocol Notes

Target: Windows WeChat `4.1.9.23`.

## Evidence

- `FinderLive.publishBrxAxlcRME.js`: live-room Init/Join flow and comment payload.
- `virtual_svg-icons-register.publishB3arVus6aL.js`: Finder API descriptors and parameter normalization.

The live page uses one complete URL for all H5ExtTransfer calls. Numeric IDs use
Base64URL without padding over eight-byte big-endian uint64 values:

```text
https://channels.weixin.qq.com/web/pages/live?oid=...&nid=...&context_id=...&entrance_id=...&sub_entranceid=...
```

Search live cards commonly return an `export/...` encrypted token instead. That
route must use `eid` and omit the raw decimal nonce:

```text
https://channels.weixin.qq.com/web/pages/live?eid=export%2F...&entrance_id=88
```

All calls use scope `finderLive`, `jsapiControlByte=400`, an empty transfer CGI URL, and `reportSecurity=false`.

The generic H5 request layout initializes `+0xA8` empty because that field is
`pub_key_token`; the submit manager replaces it from its active configuration.
Request `+0xC8` is the manager-selected `cgi_id`. Neither field is populated
from an H5 auth token or H5 version argument.

## Request Chain

1. `pc_findergetcommentdetail`, cmd `5259`, resolves `encrypted_objectid`
   into `object.id`, `object.object_nonce_id`, and `object.live_info.live_id`.
2. `pc_finderinit`, cmd `5831`, obtains top-level `userver_for_h5`,
   `myacct[0].username`, and `current_alias_role_type`.
3. `pc_finderjoinlive`, cmd `4233`, sends the current Finder account username
   only for role 3 and obtains top-level `live_cookies`.
4. `pc_finderpostlivemsg`, cmd `4145`, posts the comment.

The detail request uses `encrypted_objectid` for `export/...`; it does not put
that token into JoinLive's numeric `object_id` field.

The first Init request does not include `live_identity`. The role returned by
Init is used in the base request for Join and Post unless the caller explicitly
overrides it.

The normalized role-3 Join request is:

```json
{
  "finder_basereq": {
    "scene": 142,
    "userver_for_h5": "...",
    "live_identity": 1
  },
  "live_id": "...",
  "object_id": "...",
  "object_nonce_id": "...",
  "client_status": {"video_decoder_support_mask": 1},
  "finder_username": "<myacct[0].username>",
  "scene": 2
}
```

The final normalized Post request is:

```json
{
  "finder_basereq": {
    "scene": 142,
    "userver_for_h5": "...",
    "live_identity": 1,
    "ctx_info": {
      "context_id": "...",
      "client_report_buff": "{\"entranceId\":\"88\",\"subEntranceid\":\"...\"}"
    }
  },
  "live_cookies": "...",
  "msg": {
    "content": "comment",
    "type": 1
  },
  "live_id": "...",
  "client_msg_id": "pc_<myacct[0].username>_<unique suffix>",
  "object_id": "..."
}
```

For role 1 with `myacct=[]`, Join and Post both omit `finder_username`.
`wx_username_for_h5` is used by the page's identity/UI state and is not a
protocol fallback for that field. In this branch the official default message
ID stringifies the missing account username as `pc_undefined_<unique suffix>`.
For role 3, Join and Post use `myacct[0].username`; they never use the live
anchor username returned by search.

The H5 callback can wrap the CGI JSON in `jsapi_resp.resp_json`; native code unwraps that string before extracting session values or returning the response.
