from __future__ import annotations

import ctypes
import os
import re
import struct
from ctypes import POINTER, byref, c_int, c_uint32, c_ulonglong, c_void_p, c_wchar, c_wchar_p, create_string_buffer, create_unicode_buffer, sizeof, windll
from typing import Any, Callable, Dict, List, Optional

kernel32 = windll.kernel32
psapi = windll.psapi
user32 = windll.user32

PROCESS_ALL_ACCESS = 0x1F0FFF
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
WM_NULL = 0x0000


def _setup_fn(dll: Any, name: str, args: list[Any], restype: Any) -> None:
    fn = getattr(dll, name)
    fn.argtypes = args
    fn.restype = restype


_setup_fn(kernel32, "CreateToolhelp32Snapshot", [c_uint32, c_uint32], c_void_p)
_setup_fn(kernel32, "Process32FirstW", [c_void_p, c_void_p], c_int)
_setup_fn(kernel32, "Process32NextW", [c_void_p, c_void_p], c_int)
_setup_fn(kernel32, "CloseHandle", [c_void_p], c_int)
_setup_fn(kernel32, "OpenProcess", [c_uint32, c_int, c_uint32], c_void_p)
_setup_fn(kernel32, "GetLastError", [], c_uint32)
_setup_fn(kernel32, "QueryFullProcessImageNameW", [c_void_p, c_uint32, c_wchar_p, POINTER(c_uint32)], c_int)
_setup_fn(kernel32, "ReadProcessMemory", [c_void_p, c_void_p, c_void_p, c_ulonglong, POINTER(c_ulonglong)], c_int)
_setup_fn(kernel32, "VirtualQueryEx", [c_void_p, c_void_p, c_void_p, c_ulonglong], c_ulonglong)
_setup_fn(psapi, "EnumProcessModules", [c_void_p, POINTER(c_void_p), c_uint32, POINTER(c_uint32)], c_int)
_setup_fn(psapi, "GetModuleFileNameExW", [c_void_p, c_void_p, c_wchar_p, c_uint32], c_uint32)
_setup_fn(user32, "EnumWindows", [c_void_p, c_void_p], c_int)
_setup_fn(user32, "GetClassNameW", [c_void_p, c_wchar_p, c_int], c_int)
_setup_fn(user32, "GetWindowTextW", [c_void_p, c_wchar_p, c_int], c_int)
_setup_fn(user32, "GetWindowThreadProcessId", [c_void_p, POINTER(c_uint32)], c_uint32)
_setup_fn(user32, "IsWindowVisible", [c_void_p], c_int)
_setup_fn(user32, "PostMessageW", [c_void_p, c_uint32, c_void_p, c_void_p], c_int)
_setup_fn(user32, "PostThreadMessageW", [c_uint32, c_uint32, c_void_p, c_void_p], c_int)

SYSTEM_CONTACT_PREFIXES = (
    "gh_", "weixin", "filehelp", "qqmail", "weibo", "floatbottle", "medianote",
    "message", "mphelper", "newsapp", "pmnew2010", "qqsafe",
)


def contact_category(item: Dict[str, Any]) -> str:
    wxid = str(item.get("wxid") or "")
    if "@chatroom" in wxid:
        return "room"
    if any(prefix in wxid for prefix in SYSTEM_CONTACT_PREFIXES):
        return "system"
    state_raw = str(item.get("state") or "")
    try:
        state = int(state_raw, 16) if state_raw else 0
    except ValueError:
        state = 0
    if state == 2:
        return "deleted"
    return "friend"


def is_contact_wxid_candidate(wxid: str) -> bool:
    wxid = str(wxid or "")
    if not wxid:
        return False
    if wxid in ("filehelper",):
        return True
    if wxid.startswith("wxid_") or "@chatroom" in wxid:
        return True
    if any(prefix in wxid for prefix in SYSTEM_CONTACT_PREFIXES):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{4,31}", wxid))

def find_wechat_processes() -> List[Dict[str, Any]]:
    """返回 [{pid, name, exe_path, weixin_base, weixin_size}]。"""
    results = []
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == -1:
        return results

    class PE(ctypes.Structure):
        _fields_ = [("sz", c_uint32), ("u1", c_uint32), ("pid", c_uint32),
                    ("u2", c_ulonglong), ("u3", c_uint32), ("thr", c_uint32),
                    ("ppid", c_uint32), ("pri", c_int), ("flg", c_uint32),
                    ("exe", c_wchar * 260)]
    entry = PE(); entry.sz = sizeof(PE)

    if not kernel32.Process32FirstW(snap, byref(entry)):
        kernel32.CloseHandle(snap)
        return results

    while True:
        name = entry.exe.lower()
        if re.fullmatch(r"(?:wechat\d*|weixin\d*|weixi\d+)\.exe", name):
            info = {"pid": entry.pid, "name": entry.exe}
            # 尝试获取 Weixin.dll 基址
            h = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, entry.pid)
            if h:
                exe_buf = create_unicode_buffer(1024)
                exe_len = c_uint32(len(exe_buf))
                if kernel32.QueryFullProcessImageNameW(h, 0, exe_buf, byref(exe_len)):
                    info["exe_path"] = exe_buf.value
                mods = (c_void_p * 2048)()
                needed = c_uint32(0)
                psapi.EnumProcessModules(h, mods, sizeof(mods), byref(needed))
                count = needed.value // sizeof(c_void_p)
                buf = create_unicode_buffer(260)
                for i in range(count):
                    psapi.GetModuleFileNameExW(h, mods[i], buf, 260)
                    if os.path.basename(buf.value).lower() in {"weixin.dll", name[:-4] + ".dll"}:
                        info["weixin_base"] = mods[i] or 0
                        # 读 PE 头取 SizeOfImage
                        from ctypes import c_void_p as vp

                        class RPM_WRAP:
                            @staticmethod
                            def read(handle, addr, size):
                                buf2 = create_string_buffer(size)
                                n = c_ulonglong(0)
                                windll.kernel32.ReadProcessMemory(handle, vp(addr), buf2, size, ctypes.byref(n))
                                return buf2.raw[:n.value]
                        raw = RPM_WRAP.read(h, info["weixin_base"], 0x40)
                        pe_off = struct.unpack_from("<I", raw, 0x3C)[0]
                        pe = RPM_WRAP.read(h, info["weixin_base"] + pe_off, 0x60)
                        info["weixin_size"] = struct.unpack_from("<I", pe, 0x50)[0]
                        break
                kernel32.CloseHandle(h)
            results.append(info)
        if not kernel32.Process32NextW(snap, byref(entry)):
            break
    kernel32.CloseHandle(snap)
    return results


def find_wechat_windows(pid: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return visible WeChat windows with their UI thread ids."""
    windows: List[Dict[str, Any]] = []
    enum_proc_type = ctypes.WINFUNCTYPE(c_int, c_void_p, c_void_p)

    def on_window(hwnd: int, lparam: int) -> int:
        del lparam
        owner_pid = c_uint32(0)
        tid = user32.GetWindowThreadProcessId(hwnd, byref(owner_pid))
        if pid is not None and owner_pid.value != pid:
            return 1

        cls = create_unicode_buffer(256)
        title = create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, cls, len(cls))
        user32.GetWindowTextW(hwnd, title, len(title))
        is_visible = bool(user32.IsWindowVisible(hwnd))
        if cls.value == "Qt51514QWindowIcon" or (is_visible and owner_pid.value):
            windows.append({
                "hwnd": hwnd,
                "pid": owner_pid.value,
                "tid": tid,
                "class": cls.value,
                "title": title.value,
                "visible": is_visible,
            })
        return 1

    cb = enum_proc_type(on_window)
    user32.EnumWindows(cb, 0)
    windows.sort(key=lambda item: (item["class"] != "Qt51514QWindowIcon", not item["visible"]))
    return windows


def choose_wechat_target() -> Optional[Dict[str, Any]]:
    procs = find_wechat_processes()
    if not procs:
        return None
    target_pid = int(os.environ.get("WX_TARGET_PID") or "0")
    main = None
    wins: List[Dict[str, Any]] = []
    if target_pid:
        main = next((p for p in procs if int(p.get("pid") or 0) == target_pid), None)
        if not main or not main.get("weixin_base"):
            return None
        wins = find_wechat_windows(main["pid"])
    if not main:
        proc_by_pid = {int(p.get("pid") or 0): p for p in procs}
        wins = [
            window
            for window in find_wechat_windows()
            if window.get("visible") and int(window.get("pid") or 0) in proc_by_pid
        ]
        if wins:
            main = proc_by_pid[int(wins[0]["pid"])]
            wins = [wins[0]]
        else:
            main = next((p for p in procs if p.get("weixin_base", 0)), procs[0])
    target = dict(main)
    if wins:
        target.update(wins[0])
    return target


def rpm(handle: int, addr: int, size: int) -> bytes:
    buf = create_string_buffer(size)
    got = c_ulonglong(0)
    ok = kernel32.ReadProcessMemory(handle, c_void_p(addr), buf, size, byref(got))
    if not ok or got.value == 0:
        return b""
    return buf.raw[:got.value]


def rpm_i32(handle: int, addr: int) -> int:
    data = rpm(handle, addr, 4)
    return struct.unpack("<i", data)[0] if len(data) == 4 else 0


def rpm_u64(handle: int, addr: int) -> int:
    data = rpm(handle, addr, 8)
    return struct.unpack("<Q", data)[0] if len(data) == 8 else 0


def pattern_scan(data: bytes, pattern: str) -> int:
    tokens = pattern.split()
    pats: List[Optional[int]] = []
    for token in tokens:
        pats.append(None if token in ("?", "??") else int(token, 16))
    end = len(data) - len(pats) + 1
    for i in range(max(0, end)):
        ok = True
        for j, val in enumerate(pats):
            if val is not None and data[i + j] != val:
                ok = False
                break
        if ok:
            return i
    return -1


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", c_ulonglong),
        ("AllocationBase", c_ulonglong),
        ("AllocationProtect", c_uint32),
        ("__alignment1", c_uint32),
        ("RegionSize", c_ulonglong),
        ("State", c_uint32),
        ("Protect", c_uint32),
        ("Type", c_uint32),
        ("__alignment2", c_uint32),
    ]


def is_readable_page(protect: int) -> bool:
    return bool(protect) and not (protect & PAGE_NOACCESS) and not (protect & PAGE_GUARD)


def search_process_qword(handle: int, value: int, max_hits: int = 20000) -> List[int]:
    needle = struct.pack("<Q", value)
    hits: List[int] = []
    mbi = MEMORY_BASIC_INFORMATION64()
    addr = 0x10000
    max_addr = 0x7FFFFFFFFFFF
    while addr < max_addr and len(hits) < max_hits:
        got = kernel32.VirtualQueryEx(handle, c_void_p(addr), byref(mbi), sizeof(mbi))
        if not got:
            addr += 0x10000
            continue
        base = int(mbi.BaseAddress)
        size = int(mbi.RegionSize)
        if size <= 0:
            addr += 0x10000
            continue
        if mbi.State == MEM_COMMIT and is_readable_page(mbi.Protect) and size <= 64 * 1024 * 1024:
            data = rpm(handle, base, size)
            pos = 0
            while data:
                pos = data.find(needle, pos)
                if pos < 0:
                    break
                hits.append(base + pos)
                if len(hits) >= max_hits:
                    break
                pos += 1
        addr = base + size
    return hits


def read_wx_string_remote(handle: int, addr: int, indirect: bool = False, max_len: int = 4096) -> str:
    if indirect:
        addr = rpm_u64(handle, addr)
        if not addr:
            return ""
    length = rpm_i32(handle, addr + 0x10)
    tag = rpm_i32(handle, addr + 0x18)
    if length <= 0 or length > max_len or tag <= 0:
        return ""
    src = addr if tag == 15 else rpm_u64(handle, addr)
    if not src:
        return ""
    raw_len = length if tag == 15 else min(length * 2, max_len * 2)
    raw = rpm(handle, src, raw_len)
    if not raw:
        return ""
    for enc in ("utf-8", "utf-16-le"):
        try:
            return raw[:length if enc == "utf-8" else min(len(raw), length * 2)].decode(enc, errors="ignore").rstrip("\x00")
        except Exception:
            pass
    return ""


def read_plain_c_string(handle: int, addr: int, max_len: int = 4096, encoding: str = "utf-8") -> str:
    if not addr:
        return ""
    raw = rpm(handle, addr, max_len)
    if not raw:
        return ""
    raw = raw.split(b"\x00", 1)[0]
    if not raw:
        return ""
    try:
        return raw.decode(encoding, errors="ignore")
    except Exception:
        return ""


def read_userinfo_string(handle: int, base_addr: int, field_off: int, max_len: int = 4096, utf8: bool = True) -> str:
    length = rpm_i32(handle, base_addr + field_off + 0x10)
    tag = rpm_i32(handle, base_addr + field_off + 0x18)
    if length <= 0:
        return ""
    if length > max_len:
        length = max_len
    src = base_addr + field_off if tag <= 15 else rpm_u64(handle, base_addr + field_off)
    if not src:
        return ""
    raw = rpm(handle, src, length)
    if not raw:
        return ""
    encodings = ("utf-8", "gbk", "utf-16-le") if utf8 else ("gbk", "utf-8", "utf-16-le")
    for enc in encodings:
        try:
            text = raw.decode(enc, errors="ignore").rstrip("\x00")
            if text:
                return text
        except Exception:
            pass
    return ""


def scan_self_profile(
    *,
    get_contact_map: Optional[Callable[[], Dict[str, Dict[str, Any]]]] = None,
    save_profile: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    target = choose_wechat_target()
    if not target or not target.get("weixin_base"):
        return {"ok": False, "error": "wechat_not_found"}
    pid = int(target["pid"])
    base = int(target["weixin_base"])
    size = int(target.get("weixin_size") or 0)
    if not size:
        return {"ok": False, "error": "weixin_size_unknown"}
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return {"ok": False, "error": f"OpenProcess failed: {kernel32.GetLastError()}"}
    try:
        image = rpm(h, base, size)
        pattern = "48 8B 0D ?? ?? ?? ?? 48 8B 01 48 8D 55 D8 FF 50 60"
        off = pattern_scan(image, pattern)
        if off < 0:
            return {"ok": False, "error": "login_profile_pattern_not_found"}
        rel = struct.unpack_from("<i", image, off + 3)[0]
        manager_ptr_addr = base + off + 7 + rel
        user_manager = rpm_u64(h, manager_ptr_addr)
        user_info = rpm_u64(h, user_manager + 0x68) if user_manager else 0
        if not user_info:
            return {"ok": False, "error": "not_logged_in", "manager_ptr_addr": f"0x{manager_ptr_addr:X}"}

        profile = {
            "ok": True,
            "pid": pid,
            "pattern_addr": f"0x{base + off:X}",
            "manager_ptr_addr": f"0x{manager_ptr_addr:X}",
            "user_manager": f"0x{user_manager:X}",
            "user_info": f"0x{user_info:X}",
            "wxid": read_userinfo_string(h, user_info, 0x48),
            "account": read_userinfo_string(h, user_info, 0x68),
            "nickname": read_userinfo_string(h, user_info, 0x88),
            "mobile": read_userinfo_string(h, user_info, 0xA8, utf8=False),
            "signature": read_userinfo_string(h, user_info, 0xD0),
            "city": read_userinfo_string(h, user_info, 0xF0, utf8=False),
            "province": read_userinfo_string(h, user_info, 0x110, utf8=False),
            "country": read_userinfo_string(h, user_info, 0x130, utf8=False),
            "sns_background": read_plain_c_string(h, rpm_u64(h, user_info + 0x180)),
            "device_type": read_userinfo_string(h, user_info, 0x1B0, utf8=False),
        }
        contact_map = get_contact_map() if get_contact_map else {}
        contact = contact_map.get(profile.get("wxid") or "", {})
        if contact:
            profile["head_image"] = str(contact.get("head_image") or "")
            profile["contact"] = contact
        if save_profile:
            save_profile(profile)
        return profile
    finally:
        kernel32.CloseHandle(h)


def scan_contacts(limit: int = 5000) -> Dict[str, Any]:
    target = choose_wechat_target()
    if not target or not target.get("weixin_base"):
        return {"ok": False, "error": "wechat_not_found"}
    pid = int(target["pid"])
    base = int(target["weixin_base"])
    size = int(target.get("weixin_size") or 0)
    if not size:
        return {"ok": False, "error": "weixin_size_unknown"}
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return {"ok": False, "error": f"OpenProcess failed: {kernel32.GetLastError()}"}
    try:
        image = rpm(h, base, size)
        pattern = "48 8D 05 ?? ?? ?? ?? 49 89 06 4C 89 B5 58 01 00 00"
        off = pattern_scan(image, pattern)
        if off < 0:
            return {"ok": False, "error": "contact_pattern_not_found"}
        rel = struct.unpack_from("<i", image, off + 3)[0]
        target_addr = base + off + 7 + rel
        candidates = search_process_qword(h, target_addr, max_hits=limit * 20)
        contacts: List[Dict[str, Any]] = []
        seen = set()
        for addr in candidates[:limit]:
            wxid = read_wx_string_remote(h, addr + 0x18)
            if not wxid or wxid in seen:
                continue
            if not is_contact_wxid_candidate(wxid):
                continue
            seen.add(wxid)
            contacts.append({
                "wxid": wxid,
                "account": read_wx_string_remote(h, addr + 0x38),
                "v3": read_wx_string_remote(h, addr + 0x58),
                "remark": read_wx_string_remote(h, addr + 0x88),
                "nickname": read_wx_string_remote(h, addr + 0xE8),
                "head_image": read_wx_string_remote(h, addr + 0x148),
                "signature": read_wx_string_remote(h, addr + 0x210, indirect=True),
                "city": " ".join(filter(None, [
                    read_wx_string_remote(h, addr + 0x218, indirect=True),
                    read_wx_string_remote(h, addr + 0x220, indirect=True),
                    read_wx_string_remote(h, addr + 0x228, indirect=True),
                ])),
                "state": rpm(h, addr + 0x7C, 1).hex(),
                "attr": rpm(h, addr + 0x7D, 1).hex(),
                "_addr": f"0x{addr:X}",
            })
            contacts[-1]["category"] = contact_category(contacts[-1])
        return {
            "ok": True,
            "pid": pid,
            "pattern_addr": f"0x{base + off:X}",
            "target_addr": f"0x{target_addr:X}",
            "candidate_count": len(candidates),
            "count": len(contacts),
            "category_counts": {
                key: sum(1 for item in contacts if item.get("category") == key)
                for key in ("friend", "room", "system", "deleted")
            },
            "contacts": contacts,
        }
    finally:
        kernel32.CloseHandle(h)


