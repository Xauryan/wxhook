from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_find_wechat_processes: Optional[Callable[[], List[Dict[str, Any]]]] = None
_now_iso: Optional[Callable[[], str]] = None
UPDATE_LOCK_FILE: Path
UPDATE_DISABLED_SUFFIX = ".wxbridge-disabled.bak"
UPDATE_PLACEHOLDER_TEXT = "disabled by wx_direct_bridge update-lock\n"


def configure_update_lock(
    *,
    update_lock_file: Path,
    find_wechat_processes: Callable[[], List[Dict[str, Any]]],
    now_iso: Callable[[], str],
) -> None:
    global UPDATE_LOCK_FILE, _find_wechat_processes, _now_iso
    UPDATE_LOCK_FILE = Path(update_lock_file)
    _find_wechat_processes = find_wechat_processes
    _now_iso = now_iso


def _require_configured() -> None:
    if _find_wechat_processes is None or _now_iso is None:
        raise RuntimeError("wechat_update_lock is not configured")


def now_iso() -> str:
    if _now_iso is None:
        return time.strftime("%Y-%m-%dT%H:%M:%S")
    return _now_iso()

def _load_update_lock_manifest() -> Dict[str, Any]:
    if not UPDATE_LOCK_FILE.exists():
        return {"version": 1, "actions": []}
    try:
        data = json.loads(UPDATE_LOCK_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("actions"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "actions": []}


def _save_update_lock_manifest(manifest: Dict[str, Any]) -> None:
    UPDATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = now_iso()
    UPDATE_LOCK_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_update_placeholder(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8", errors="ignore").startswith(UPDATE_PLACEHOLDER_TEXT.strip())
    except Exception:
        return False


def _is_under_disabled_update_backup(path: Path) -> bool:
    return any(str(part).endswith(UPDATE_DISABLED_SUFFIX) for part in path.parts)


def _is_blocked_by_parent_placeholder(path: Path) -> bool:
    for parent in path.parents:
        if _is_update_placeholder(parent):
            return True
    return False


def _write_update_placeholder(path: Path, kind: str, backup: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        UPDATE_PLACEHOLDER_TEXT + f"kind={kind}\nbackup={backup}\ncreated_at={now_iso()}\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o444)
    except OSError:
        pass


def _candidate_update_roots() -> List[Path]:
    roots: Dict[str, Path] = {}

    def add(path: Optional[Path]) -> None:
        if not path:
            return
        try:
            resolved = path.expanduser()
        except Exception:
            resolved = path
        if resolved.exists():
            roots[str(resolved).lower()] = resolved

    _require_configured()
    for proc in _find_wechat_processes():
        exe_path = proc.get("exe_path")
        if exe_path:
            exe = Path(str(exe_path))
            add(exe.parent)
            add(exe.parent.parent)

    for env_name in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ProgramFiles", "ProgramFiles(x86)"):
        val = os.environ.get(env_name)
        if not val:
            continue
        base = Path(val)
        add(base / "Tencent" / "Weixin")
        add(base / "Tencent" / "WeChat")
        add(base / "Tencent" / "xwechat")

    for path in (
        Path(r"D:\Program Files\Tencent\Weixin"),
        Path(r"D:\Program Files\Tencent\WeChat"),
        Path(r"C:\Program Files\Tencent\Weixin"),
        Path(r"C:\Program Files\Tencent\WeChat"),
        Path(r"C:\Program Files (x86)\Tencent\Weixin"),
        Path(r"C:\Program Files (x86)\Tencent\WeChat"),
    ):
        add(path)

    return list(roots.values())


def find_update_targets(include_cache: bool = True) -> List[Dict[str, Any]]:
    _require_configured()
    update_names = {
        "weixinupdate.exe",
        "wechatupdate.exe",
        "weixinupdater.exe",
        "wechatupdater.exe",
    }
    targets: Dict[str, Dict[str, Any]] = {}
    for root in _candidate_update_roots():
        try:
            for path in root.rglob("*"):
                if _is_under_disabled_update_backup(path):
                    continue
                if path.is_file() and path.name.lower() in update_names:
                    targets[str(path).lower()] = {"kind": "file", "path": str(path), "reason": "updater_exe"}
        except OSError:
            continue

    if include_cache:
        appdata = os.environ.get("APPDATA")
        if appdata:
            update_dir = Path(appdata) / "Tencent" / "xwechat" / "update"
            if update_dir.exists() or Path(str(update_dir) + UPDATE_DISABLED_SUFFIX).exists():
                targets[str(update_dir).lower()] = {"kind": "dir", "path": str(update_dir), "reason": "xwechat_update_cache"}

    return sorted(targets.values(), key=lambda item: (item["kind"] == "dir", item["path"].lower()))


def update_lock_status() -> Dict[str, Any]:
    _require_configured()
    manifest = _load_update_lock_manifest()
    action_by_path = {str(item.get("path", "")).lower(): item for item in manifest.get("actions", []) if item.get("path")}
    targets_by_path: Dict[str, Dict[str, Any]] = {}
    for target in find_update_targets(include_cache=True):
        path = Path(target["path"])
        backup = Path(str(path) + UPDATE_DISABLED_SUFFIX)
        action = action_by_path.get(str(path).lower())
        locked = (backup.exists() and _is_update_placeholder(path)) or _is_blocked_by_parent_placeholder(path)
        active = path.exists() and not _is_update_placeholder(path)
        targets_by_path[str(path).lower()] = {
            **target,
            "exists": path.exists(),
            "active": active,
            "locked": locked,
            "backup": str(backup),
            "backup_exists": backup.exists(),
            "manifested": bool(action),
        }
    for action in manifest.get("actions", []):
        path = Path(str(action.get("path") or ""))
        if not str(path) or str(path).lower() in targets_by_path:
            continue
        backup = Path(str(action.get("backup") or str(path) + UPDATE_DISABLED_SUFFIX))
        targets_by_path[str(path).lower()] = {
            "kind": action.get("kind"),
            "path": str(path),
            "reason": action.get("reason", "manifest"),
            "exists": path.exists(),
            "active": path.exists() and not _is_update_placeholder(path),
            "locked": backup.exists() or _is_update_placeholder(path) or _is_blocked_by_parent_placeholder(path),
            "backup": str(backup),
            "backup_exists": backup.exists(),
            "manifested": True,
        }
    targets = sorted(targets_by_path.values(), key=lambda item: (str(item.get("kind")) == "dir", str(item.get("path")).lower()))
    return {
        "ok": True,
        "manifest": str(UPDATE_LOCK_FILE),
        "locked_count": sum(1 for item in targets if item["locked"]),
        "active_count": sum(1 for item in targets if item["active"]),
        "targets": targets,
    }


def lock_wechat_update(include_cache: bool = True) -> Dict[str, Any]:
    _require_configured()
    manifest = _load_update_lock_manifest()
    actions = [item for item in manifest.get("actions", []) if item.get("path")]
    action_paths = {str(item.get("path")).lower() for item in actions}
    results = []

    for target in find_update_targets(include_cache=include_cache):
        path = Path(target["path"])
        backup = Path(str(path) + UPDATE_DISABLED_SUFFIX)
        key = str(path).lower()
        item = {**target, "backup": str(backup)}
        try:
            if backup.exists() and _is_update_placeholder(path):
                item.update({"ok": True, "status": "already_locked"})
            elif target["kind"] == "file":
                if not path.is_file():
                    item.update({"ok": False, "status": "missing"})
                else:
                    if backup.exists():
                        backup = Path(str(path) + f".{int(time.time())}{UPDATE_DISABLED_SUFFIX}")
                        item["backup"] = str(backup)
                    shutil.move(str(path), str(backup))
                    _write_update_placeholder(path, "file", backup)
                    item.update({"ok": True, "status": "locked"})
            elif target["kind"] == "dir":
                if path.is_file() and _is_update_placeholder(path):
                    item.update({"ok": True, "status": "already_locked"})
                elif not path.is_dir():
                    item.update({"ok": False, "status": "missing"})
                else:
                    if backup.exists():
                        backup = Path(str(path) + f".{int(time.time())}{UPDATE_DISABLED_SUFFIX}")
                        item["backup"] = str(backup)
                    shutil.move(str(path), str(backup))
                    _write_update_placeholder(path, "dir", backup)
                    item.update({"ok": True, "status": "locked"})
            else:
                item.update({"ok": False, "status": "unsupported_kind"})
        except Exception as exc:
            item.update({"ok": False, "status": "error", "error": str(exc)})

        if item.get("ok") and key not in action_paths:
            actions.append({
                "kind": target["kind"],
                "path": str(path),
                "backup": item["backup"],
                "reason": target.get("reason", ""),
                "created_at": now_iso(),
            })
            action_paths.add(key)
        results.append(item)

    manifest = {"version": 1, "actions": actions}
    _save_update_lock_manifest(manifest)
    return {
        "ok": all(item.get("ok") for item in results),
        "manifest": str(UPDATE_LOCK_FILE),
        "results": results,
    }


def unlock_wechat_update() -> Dict[str, Any]:
    _require_configured()
    manifest = _load_update_lock_manifest()
    remaining = []
    results = []
    actions = sorted(manifest.get("actions", []), key=lambda item: str(item.get("kind")) == "file")
    for action in actions:
        path = Path(str(action.get("path") or ""))
        backup = Path(str(action.get("backup") or ""))
        item = {"kind": action.get("kind"), "path": str(path), "backup": str(backup)}
        try:
            if path.exists() and _is_update_placeholder(path):
                try:
                    os.chmod(path, 0o666)
                except OSError:
                    pass
                path.unlink()
            if backup.exists():
                shutil.move(str(backup), str(path))
                item.update({"ok": True, "status": "restored"})
            else:
                item.update({"ok": True, "status": "backup_missing_placeholder_removed"})
        except Exception as exc:
            item.update({"ok": False, "status": "error", "error": str(exc)})
            remaining.append(action)
        results.append(item)

    _save_update_lock_manifest({"version": 1, "actions": remaining})
    return {
        "ok": all(item.get("ok") for item in results),
        "manifest": str(UPDATE_LOCK_FILE),
        "results": results,
    }


