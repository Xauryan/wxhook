# wx_hook_bridge Native Refactor

This directory is the maintainable source layout for `wx_hook_bridge.dll`.

The root `wx_hook_bridge.dll.c` is preserved as the legacy monolith and reference snapshot. The default Python build path now targets `src/wx_hook_bridge.c`, which is a normal header-only entry translation unit linked with standalone modules under `src/modules/`. Export names and runtime behavior stay stable while implementation code moves behind explicit cross-domain headers.

This is no longer only an include-split. `build.ps1` compiles the transitional core plus real standalone modules and links them as separate objects:

| Object | Source | Responsibility |
| --- | --- | --- |
| `wx_hook_bridge.obj` | `src/wx_hook_bridge.c` | Native bridge entry translation unit and cross-module header wiring |
| `state.obj` | `src/modules/state.c` | Private process runtime context, Weixin module lookup, and self-wxid identity accessors |
| `memory.obj` | `src/modules/memory.c` | SEH-safe native memory reads/writes, readable-region checks, near-code allocation, byte-copy helpers |
| `file_io.obj` | `src/modules/file_io.c` | Bridge file protocol IO and directory creation |
| `text.obj` | `src/modules/text.c` | Numeric parsing, JSON/XML escaping, string matching, ID parsing, query extraction |
| `protocol.obj` | `src/modules/protocol.c` | HTTP push, status/result files, hongbao control flags, patch-restore helper |
| `send_runtime.obj` | `src/modules/send_runtime.c` | Shared send runtime helpers, arena retention, UTF-8 file metadata, queue submission |
| `wechat_string.obj` | `src/modules/wechat_string.c` | WeChat string readers, std-string writers, self-wxid learning, revoke payload cast |
| `contact_lookup.obj` | `src/modules/contact_lookup.c` | Contact and room-member display-name lookup from the bridge contact cache |
| `revoke_protocol.obj` | `src/modules/revoke_protocol.c` | Revoke XML parsing, revocable message classification, revoke type labels |
| `debug_log.obj` | `src/modules/debug_log.c` | Quote, forward, and hongbao diagnostic log appenders |
| `forward_protocol.obj` | `src/modules/forward_protocol.c` | Forward source markers, subtype/kind mapping, synthetic forward ID generation |
| `hongbao_runtime.obj` | `src/modules/hongbao_runtime.c` | Private Hongbao runtime/status context, receive snapshots, route snapshots, open/deferred-open state |
| `hongbao_status.obj` | `src/modules/hongbao_status.c` | Hongbao state serialization and receive-candidate logging |
| `hongbao_trace.obj` | `src/modules/hongbao_trace.c` | Shared hongbao trace JSON fields, probe-string readers, URL/query extraction, and source-vector hongbao detection |
| `hongbao_control.obj` | `src/modules/hongbao_control.c` | Hongbao control polling, page context capture, deferred auto-open, and parser hook |
| `hongbao_sentinels.obj` | `src/modules/hongbao_sentinels.c` | Hongbao sentinel probe hook table, sentinel trampolines, and route-candidate probe |
| `hongbao_receive_route.obj` | `src/modules/hongbao_receive_route.c` | Hongbao task-factory, receive-route, route-vector, route-context, and classifier hook bodies |
| `hongbao_noclick.obj` | `src/modules/hongbao_noclick.c` | Hongbao no-click private runtime state, pending route/source queue, and source-submit hook body |
| `hongbao_cold.obj` | `src/modules/hongbao_cold.c` | Cold claim (冷抢): FillFromLocalMsg → Service_Start* → deferred Launch_receive/open; ParseExtra timing; pin bags |
| `hongbao_hooks.obj` | `src/modules/hongbao_hooks.c` | Hongbao hook installation and teardown wrappers |
| `anti_revoke.obj` | `src/modules/anti_revoke.c` | Anti-revoke patch enable/disable and persisted state restore |
| `worker_control.obj` | `src/modules/worker_control.c` | Worker ownership token and outbox cleanup helpers |
| `media_ops.obj` | `src/modules/media_ops.c` | Internal WeChat image download and decode operations |
| `revoke_notice_event.obj` | `src/modules/revoke_notice_event.c` | Synthetic revoke notice event JSON/XML bridge |
| `revoke_hooks.obj` | `src/modules/revoke_hooks.c` | Revoke entry/dispatch/real hook metadata, trampolines, installation, teardown, stale-site restore, and real-revoke payload handling |
| `room_contacts.obj` | `src/modules/room_contacts.c` | Room/contact scanning, private auto-scan state, auto room-member scheduling, and room task lifecycle |
| `forward_builders.obj` | `src/modules/forward_builders.c` | Forward manager/controller lookup and forward source model/send-item builders |
| `forward_retained.obj` | `src/modules/forward_retained.c` | Retained forward send-item/model cache, persisted retained-pointer index, and WeChat model/send-item copy ownership |
| `forward_send.obj` | `src/modules/forward_send.c` | Native forward queue and high/dispatch submit paths, including forward trace hook bodies |
| `forward_hooks.obj` | `src/modules/forward_hooks.c` | Forward UI/submit/dispatch trace hook installation and teardown |
| `send_api.obj` | `src/modules/send_api.c` | Quote reply, text send, and image send entry helpers |
| `card_send.obj` | `src/modules/card_send.c` | Native appmsg/card send bridge |
| `outbox_dispatch.obj` | `src/modules/outbox_dispatch.c` | Bridge outbox parser and command dispatch |
| `revoke_forward_cache.obj` | `src/modules/revoke_forward_cache.c` | Message cache, filehelper-copy matching, pending quote jobs, and revoke forward job ownership |
| `receive_hooks.obj` | `src/modules/receive_hooks.c` | Receive processing, receive hook installation, async queue hook installation, and incoming revoke sysmsg detection |
| `lifecycle_exports.obj` | `src/modules/lifecycle_exports.c` | Initialization lifecycle, revoke-real hook, worker loop, and exported DLL entrypoints |

Shared declarations and constants live under `src/include/`. The core uses those headers instead of keeping duplicate `static` helper definitions. Cross-domain layer calls are now grouped by explicit boundary headers such as `wx_bridge_room_contacts.h`, `wx_bridge_send_api.h`, `wx_bridge_forward_send.h`, `wx_bridge_forward_retained.h`, `wx_bridge_forward_hooks.h`, `wx_bridge_receive_hooks.h`, `wx_bridge_hongbao_noclick.h`, `wx_bridge_hongbao_trace.h`, `wx_bridge_revoke_cache.h`, `wx_bridge_revoke_forward.h`, `wx_bridge_revoke_hooks.h`, `wx_bridge_hongbao_hooks.h`, and the private hook-body boundaries `wx_bridge_forward_entrypoints.h` and `wx_bridge_hongbao_entrypoints.h`.

## Legacy References

The root `wx_hook_bridge.dll.c` remains the only legacy monolith reference snapshot. The old transitional `src/layers/` split snapshots were removed after their code was promoted to real linked modules. Keeping duplicate C snapshots had become an architectural risk because they preserved stale global-state patterns that are no longer valid.

The maintainable source of truth is now `src/wx_hook_bridge.c`, `src/modules/`, and `src/include/`.

## Build

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File native\wx_hook_bridge\build.ps1
```

Python callers can use the same build path:

```powershell
python native\wx_hook_bridge\build.py --out-file native\wx_hook_bridge\build\wx_hook_bridge.refactor.dll
```

The default output is the repository `dist\wx_hook_bridge.dll`, so existing inject/runtime code can keep loading the same DLL path. Override with `-OutFile` when a side-by-side artifact is needed.

## Next Decomposition Step

No-click runtime counters, busy flag, duplicate send-id tracking, click-state stub, and retained red send-item pointer now live in a private `HbNoclickState` inside `hongbao_noclick.c`; receive processing enters through `hb_noclick_remember_red_send_item()` instead of writing no-click globals directly.

Room/contact task state, auto-room scan cache, pending auto-room request fields, and auto-contact scan flags now live in a private `RoomContactsState` inside `room_contacts.c`; callers use the room/contact module API instead of shared global storage.

Outbox processing ownership, worker-owner token state, send-runtime retained arenas, and anti-revoke saved patch bytes are now private to their respective modules instead of being exported through a shared global-state header.

Revoke/cache storage, filehelper-copy matching state, pending quote jobs, and revoke-notice dedupe state now live in a private `RevokeCacheState` inside `revoke_forward_cache.c`; `wx_bridge_revoke_cache.h` now exposes only cache write APIs and copy-out revoke snapshots needed by revoke hook callers.

The `CachedMsg` implementation record is now private to `revoke_forward_cache.c`. Cross-domain callers use `RevokeCachedMsgSnapshot` copy-out queries instead of borrowing ring-buffer slots.

Retained forward send-item/model rings and the persisted retained-pointer index now live in `forward_retained.c`; callers use `wx_bridge_forward_retained.h`, and the verifier prevents retained-forward state from drifting back into the revoke cache module.

Forward hook/runtime state, forward manager/controller lookup caches, receive/async/revoke entry/dispatch hook metadata, Hongbao hook metadata, and Hongbao expected byte signatures are now private to their owning modules and exposed only through explicit install/original-call/status APIs.

Revoke entry/dispatch hook state and trampolines now live in `revoke_hooks.c`; receive processing no longer owns revoke hook install/teardown state and only emits revoke notices for received system messages.

Revoke real-hook state and payload handling now also live in `revoke_hooks.c`; lifecycle exports only orchestrate install/uninstall calls and no longer owns revoke hook bytes, trampolines, or payload parsing.

Hongbao capture/open/deferred-open state, receive-candidate fields, parser/no-click receive context, and route/mid-context diagnostics now live in a private `HbRuntimeState` inside `hongbao_runtime.c`; public callers can read snapshots/getters, while runtime mutators are restricted to hongbao modules through the private `src/modules/wx_bridge_hongbao_runtime_internal.h` boundary.

`verify_boundaries.py` now also checks for stale build-manifest entries, missing module sources in `build.ps1`, public `CachedMsg` leakage, public forward hook-body leakage, misuse of hongbao internal runtime headers, retained-forward state leaking back into revoke cache ownership, revoke hook state drifting back into receive hook ownership, and revoke real-hook state drifting back into lifecycle exports.

The old `wx_bridge_state.h` global-state bus has been removed. Process/module handles and self-wxid now live behind `wx_bridge_runtime.h`, and shared native call typedefs live in `wx_bridge_native_call.h`.

The remaining architectural work is to keep shrinking domain-local public headers where practical, especially by replacing broad typedef/state includes with narrower module-specific contracts. The build no longer depends on source-order aggregation.
