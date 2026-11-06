"""Reject links and Windows reparse points in managed snapshot paths."""
from pathlib import Path
import stat


def plain_path(path: Path) -> bool:
    info = path.lstat()
    return not (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
