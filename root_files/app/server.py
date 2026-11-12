"""Sidebar file manager for Home Assistant, USB drives, and the host system.

Only the Supervisor (172.30.32.2) can connect. Home Assistant already
authenticated the person before proxying the request.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import tarfile
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

import mounts

INGRESS_IP = "172.30.32.2"
TEXT_LIMIT = 2_000_000
UPLOAD_LIMIT = 64 * 1024**3
ZIP_FILES = 4000
ZIP_BYTES = 2 * 1024**3
EXTRACT_FILES = 8000
EXTRACT_BYTES = 4 * 1024**3
LIST_CAP = 5000
SEARCH_CAP = 400
SEARCH_DIRS = 8000
STATIC = Path(__file__).resolve().parent / "static"
MOUNT_LOCK = threading.Lock()

# Refuse to delete these host directories themselves. Files inside them
# can still be changed. A misclick on "etc" should not remove the OS.
DELETE_KEEP = {
    "",
    "mnt",
    "mnt/data",
    "boot",
    "etc",
    "usr",
    "bin",
    "sbin",
    "lib",
    "lib64",
    "opt",
    "root",
    "var",
    "home",
    "run",
    "proc",
    "sys",
    "dev",
}
ZIP_KEEP = {"", "proc", "sys", "dev", "run"}
SKIP_WALK = {"proc", "sys", "dev", "run"}

STANDARD = (
    ("config", "Config", "Home Assistant", "config", "ha"),
    ("media", "Media", "Home Assistant", "media", "ha"),
    ("share", "Share", "Home Assistant", "share", "ha"),
    ("backup", "Backups", "Home Assistant", "backup", "ha"),
    ("ssl", "Certificates", "Home Assistant", "ssl", "ha"),
    ("addons", "Add-ons", "Home Assistant", "addons", "ha"),
    ("addon_configs", "Add-on configs", "Home Assistant", "addon_configs", "ha"),
)

CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".txt": "text/plain; charset=utf-8",
    ".yaml": "text/yaml; charset=utf-8",
    ".yml": "text/yaml; charset=utf-8",
    ".json": "application/json",
    ".md": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".xml": "text/plain; charset=utf-8",
    ".conf": "text/plain; charset=utf-8",
    ".zip": "application/zip",
}


class FilesError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class StopHandling(Exception):
    pass


def demo_root() -> Path | None:
    raw = os.environ.get("FILES_DEMO")
    return Path(raw) if raw else None


def allow_all() -> bool:
    return os.environ.get("FILES_ALLOW_ALL") == "1"


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            number = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{number} {unit}"
        value /= 1024
    return f"{size} B"


def pretty_fs(fstype: str) -> str:
    return {
        "vfat": "FAT",
        "msdos": "FAT",
        "exfat": "exFAT",
        "ntfs": "NTFS",
        "ntfs3": "NTFS",
        "ext4": "ext4",
        "ext3": "ext3",
        "ext2": "ext2",
    }.get((fstype or "").lower(), fstype or "")


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def safe_rel(raw: str) -> Path:
    text = (raw or "").replace("\\", "/").strip()
    if "\x00" in text:
        raise FilesError("That path is not valid.")
    if len(text) > 4096:
        raise FilesError("That path is too long.")
    if text.startswith("/"):
        text = text[1:]
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise FilesError("That path leaves the folder.", 403)
        parts.append(part)
    return Path(*parts) if parts else Path()


def rel_text(raw: str) -> str:
    rel = safe_rel(raw)
    return "" if not rel.parts else rel.as_posix()


def safe_segment(name: str) -> str:
    cleaned = (name or "").strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or "\x00" in cleaned
    ):
        raise FilesError("Use a file name without slashes.")
    if len(cleaned) > 255:
        raise FilesError("That name is too long.")
    return cleaned


def _exists_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _usage_text(path: Path) -> str:
    """Size of a disk, not the Home Assistant volume sitting underneath a plain folder."""
    try:
        if path.stat().st_dev == path.parent.stat().st_dev:
            return ""
        return format_bytes(shutil.disk_usage(path).total)
    except OSError:
        return ""


def all_places() -> list[dict]:
    demo = demo_root()
    places: list[dict] = []
    for place_id, name, group, folder, kind in STANDARD:
        path = (demo / folder) if demo else Path("/") / folder
        places.append(
            {
                "id": place_id,
                "name": name,
                "group": group,
                "kind": kind,
                "path": path,
                "available": _exists_dir(path),
                "writable": True,
                "ejectable": False,
                "detail": "",
                "reason": "" if _exists_dir(path) else "This folder is not mounted.",
                "display_root": "/" + folder,
            }
        )

    usb_root = (demo / "places" / "usb") if demo else mounts.USB_ROOT
    if usb_root.is_dir():
        for child in sorted(usb_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            if demo is None and not mounts.is_mountpoint(child):
                continue
            detail = _usage_text(child)
            places.append(
                {
                    "id": "usb-" + child.name,
                    "name": child.name,
                    "group": "USB",
                    "kind": "usb",
                    "path": child,
                    "available": True,
                    "writable": True,
                    "ejectable": demo is None,
                    "detail": detail,
                    "reason": "",
                    "display_root": "USB/" + child.name,
                }
            )

    if demo is None:
        for item in mounts.media_mounts():
            path = Path(item["path"])
            if not path.is_dir():
                continue
            label = item["name"]
            bits = ["In Storage", pretty_fs(item["fstype"]), _usage_text(path)]
            places.append(
                {
                    "id": "storage-" + mounts.sanitize_name(label),
                    "name": label,
                    "group": "USB",
                    "kind": "media",
                    "path": path,
                    "available": True,
                    "writable": True,
                    "ejectable": False,
                    "detail": " · ".join(bit for bit in bits if bit),
                    "reason": "",
                    "display_root": "/media/" + label,
                }
            )

    host = (demo / "places" / "host") if demo else mounts.HOST_ROOT
    if demo is not None:
        host_ok = host.is_dir()
        host_message = "" if host_ok else "Host folder is missing."
    else:
        status = mounts.read_status()
        host_ok = bool(status.get("host_ok")) and mounts.is_mountpoint(host)
        host_message = "" if host_ok else (status.get("host_message") or "Host root is not mounted.")
    places.append(
        {
            "id": "host",
            "name": "Host",
            "group": "System",
            "kind": "host",
            "path": host,
            "available": host_ok,
            "writable": True,
            "ejectable": False,
            "detail": "Operating system" if host_ok else "Hidden",
            "reason": host_message,
            "display_root": "",
        }
    )
    return places


def public_place(place: dict) -> dict:
    return {key: value for key, value in place.items() if key not in {"path", "display_root"}}


def place_by_id(place_id: str) -> dict:
    for place in all_places():
        if place["id"] == place_id:
            if not place["available"]:
                raise FilesError(place["reason"] or "That folder is not available.", 403)
            return place
    raise FilesError("That folder is not available.", 404)


def jail(place: dict, raw: str) -> Path:
    root = place["path"].resolve()
    rel = safe_rel(raw)
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise FilesError("That path leaves the folder.", 403)
    return target


def join_rel(parent: str, name: str) -> str:
    folder = rel_text(parent)
    segment = safe_segment(name) if name else ""
    if not folder:
        return segment
    if not segment:
        return folder
    return f"{folder}/{segment}"


def display_path(place: dict, raw: str) -> str:
    rel = rel_text(raw)
    if place["kind"] == "host":
        return "/" + rel if rel else "/"
    root = place.get("display_root") or place["name"]
    return f"{root}/{rel}" if rel else root


def followed_kind(path: Path) -> str:
    """What a path points at, after following a symlink.

    Path.is_dir(follow_symlinks=...) needs Python 3.13. The add-on image
    is Debian bookworm, which ships 3.11, so this uses stat instead.
    """
    try:
        info = path.stat()
    except OSError:
        return ""
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return "other"


def is_dir(path: Path) -> bool:
    return followed_kind(path) == "dir"


def entry_info(path: Path) -> dict:
    link = path.is_symlink()
    try:
        info = path.lstat()
    except OSError as exc:
        return {
            "name": path.name,
            "type": "other",
            "size": None,
            "mtime": None,
            "mode": "",
            "hidden": path.name.startswith("."),
            "link": link,
            "error": exc.strerror or "unreadable",
        }
    kind = "link" if link else "other"
    followed = followed_kind(path)
    if followed == "dir":
        kind = "dir"
    elif followed == "file":
        kind = "file"
    return {
        "name": path.name,
        "type": kind,
        "size": None if kind == "dir" else info.st_size,
        "mtime": int(info.st_mtime),
        "mode": oct(stat.S_IMODE(info.st_mode))[2:].zfill(3),
        "hidden": path.name.startswith("."),
        "link": link,
    }


def disk_usage(path: Path) -> dict | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def list_dir(place: dict, raw: str) -> dict:
    target = jail(place, raw)
    if not target.exists():
        raise FilesError("That folder does not exist.", 404)
    if not target.is_dir():
        raise FilesError("That is a file.", 400)
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise FilesError(f"Cannot open that folder: {exc.strerror or exc}", 403) from exc
    children.sort(key=lambda child: (not is_dir(child), child.name.lower()))
    truncated = len(children) > LIST_CAP
    entries = [entry_info(child) for child in children[:LIST_CAP]]
    try:
        writable = place["writable"] and os.access(target, os.W_OK)
    except OSError:
        writable = False
    return {
        "place": place["id"],
        "path": rel_text(raw),
        "display": display_path(place, raw),
        "writable": writable,
        "entries": entries,
        "truncated": truncated,
        "usage": disk_usage(target),
    }


def search(place: dict, raw: str, query: str, show_hidden: bool) -> list[dict]:
    needle = (query or "").strip().lower()
    if not needle:
        return []
    if len(needle) > 200:
        raise FilesError("That search is too long.")
    root = jail(place, raw)
    if not root.is_dir():
        raise FilesError("That folder does not exist.", 404)
    base = rel_text(raw)
    matches: list[dict] = []
    visited = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > SEARCH_DIRS or len(matches) >= SEARCH_CAP:
            break
        try:
            rel_dir = Path(current).resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            dirs[:] = []
            continue
        if place["kind"] == "host" and not rel_dir.parts:
            dirs[:] = [name for name in dirs if name not in SKIP_WALK]
        for name in list(dirs) + list(files):
            if not show_hidden and name.startswith("."):
                continue
            if needle not in name.lower():
                continue
            info = entry_info(Path(current) / name)
            child = rel_dir / name
            child_text = child.as_posix()
            info["path"] = f"{base}/{child_text}" if base else child_text
            matches.append(info)
            if len(matches) >= SEARCH_CAP:
                break
    return matches


def read_text(place: dict, raw: str) -> bytes:
    target = jail(place, raw)
    if not target.is_file():
        raise FilesError("That is not a file.", 400)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise FilesError(exc.strerror or "Cannot read that file.", 403) from exc
    if size > TEXT_LIMIT:
        raise FilesError("That file is too large to edit. Download it instead.", 413)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise FilesError(exc.strerror or "Cannot read that file.", 403) from exc
    if b"\x00" in data[:8192]:
        raise FilesError("That file is not text.", 415)
    return data


def write_text(place: dict, raw: str, data: bytes) -> None:
    if len(data) > TEXT_LIMIT:
        raise FilesError("That file is too large to edit here.", 413)
    target = jail(place, raw)
    if target.exists() and target.is_dir():
        raise FilesError("That is a folder.", 400)
    if not target.parent.is_dir():
        raise FilesError("That folder does not exist.", 404)
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise FilesError(exc.strerror or "Cannot save that file.", 403) from exc


def make_dir(place: dict, raw: str, name: str) -> str:
    segment = safe_segment(name)
    target = jail(place, join_rel(raw, segment))
    if target.exists():
        raise FilesError("Something with that name is already here.", 409)
    try:
        target.mkdir()
    except OSError as exc:
        raise FilesError(exc.strerror or "Cannot create that folder.", 403) from exc
    return segment


def rename(place: dict, raw: str, name: str) -> str:
    segment = safe_segment(name)
    source = jail(place, raw)
    if source.resolve() == place["path"].resolve():
        raise FilesError("The top folder cannot be renamed.", 403)
    if not source.exists() and not source.is_symlink():
        raise FilesError("That file is gone.", 404)
    current = rel_text(raw)
    parent = current.rsplit("/", 1)[0] if "/" in current else ""
    target = jail(place, join_rel(parent, segment))
    if target.exists() and target.resolve() != source.resolve():
        raise FilesError("Something with that name is already here.", 409)
    try:
        source.rename(target)
    except OSError as exc:
        raise FilesError(exc.strerror or "Cannot rename that.", 403) from exc
    return join_rel(parent, segment)


def delete_paths(place: dict, rels: list[str]) -> None:
    if not rels:
        raise FilesError("Choose a file first.")
    ordered = sorted((rel_text(rel) for rel in rels), key=lambda item: item.count("/"), reverse=True)
    root = place["path"].resolve()
    for rel in ordered:
        if not rel:
            raise FilesError("The top folder cannot be deleted.", 403)
        if place["kind"] == "host" and rel in DELETE_KEEP:
            raise FilesError(f"{rel} cannot be deleted from here.", 403)
        target = jail(place, rel)
        if target.resolve() == root:
            raise FilesError("The top folder cannot be deleted.", 403)
        try:
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        except OSError as exc:
            raise FilesError(exc.strerror or f"Cannot delete {rel}.", 403) from exc


def next_name(path: Path) -> Path:
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 500):
        candidate = path.with_name(f"{stem} {index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FilesError("Too many copies of that name.", 409)


def copy_path(source: Path, dest: Path) -> None:
    if source.is_symlink():
        os.symlink(os.readlink(source), dest)
        return
    if source.is_dir():
        dest.mkdir()
        for child in source.iterdir():
            copy_path(child, dest / child.name)
        shutil.copystat(source, dest, follow_symlinks=False)
        return
    shutil.copy2(source, dest, follow_symlinks=False)


def transfer(op: str, from_place: dict, rels: list[str], to_place: dict, to_raw: str) -> int:
    if op not in {"copy", "cut"}:
        raise FilesError("That action is not available.")
    dest_dir = jail(to_place, to_raw)
    if not dest_dir.is_dir():
        raise FilesError("Choose a folder to paste into.", 400)
    done = 0
    for rel in rels:
        source = jail(from_place, rel)
        if source.resolve() == from_place["path"].resolve():
            raise FilesError("The top folder cannot be moved.", 403)
        if not source.exists() and not source.is_symlink():
            raise FilesError("That file is gone.", 404)
        dest = dest_dir / source.name
        try:
            if op == "cut" and dest.exists() and dest.resolve() == source.resolve():
                continue
        except OSError:
            pass
        if source.is_dir():
            try:
                dest.resolve().relative_to(source.resolve())
            except ValueError:
                pass
            else:
                raise FilesError("Cannot put a folder inside itself.", 400)
        if dest.exists() and not (op == "cut" and dest.resolve() == source.resolve()):
            dest = next_name(dest)
        jail(to_place, join_rel(to_raw, dest.name))
        try:
            if op == "cut":
                shutil.move(str(source), str(dest))
            else:
                copy_path(source, dest)
        except OSError as exc:
            raise FilesError(exc.strerror or "Cannot paste that.", 403) from exc
        done += 1
    return done


def member_dest(dest_dir: Path, place: dict, name: str) -> Path:
    raw_name = (name or "").replace("\\", "/")
    if raw_name.startswith("/") or raw_name.startswith("../"):
        raise FilesError("The archive contains an unsafe path.", 400)
    pure = PurePosixPath(raw_name)
    if pure.is_absolute() or ".." in pure.parts:
        raise FilesError("The archive contains an unsafe path.", 400)
    parts = [part for part in pure.parts if part not in {".", ""}]
    if not parts:
        raise FilesError("The archive contains an unsafe path.", 400)
    root = place["path"].resolve()
    dest = dest_dir.joinpath(*parts)
    try:
        rel = dest.relative_to(root)
    except ValueError as exc:
        raise FilesError("The archive contains an unsafe path.", 400) from exc
    return jail(place, rel.as_posix())


def extract_archive(place: dict, raw: str) -> dict:
    archive = jail(place, raw)
    if not archive.is_file():
        raise FilesError("Choose an archive file.", 400)
    name = archive.name.lower()
    dest_dir = archive.parent
    if name.endswith(".zip"):
        return _extract_zip(archive, dest_dir, place)
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz")):
        return _extract_tar(archive, dest_dir, place)
    raise FilesError("Only .zip and .tar archives can be unpacked.", 400)


def _extract_zip(archive: Path, dest_dir: Path, place: dict) -> dict:
    try:
        handle = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise FilesError("That zip file is damaged.", 400) from exc
    with handle:
        infos = [info for info in handle.infolist() if not info.is_dir()]
        if len(infos) > EXTRACT_FILES or sum(info.file_size for info in infos) > EXTRACT_BYTES:
            raise FilesError("That archive is too large.", 413)
        for info in infos:
            target = member_dest(dest_dir, place, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    return {"files": len(infos), "skipped": 0}


def _extract_tar(archive: Path, dest_dir: Path, place: dict) -> dict:
    try:
        handle = tarfile.open(archive, "r:*")
    except tarfile.TarError as exc:
        raise FilesError("That archive is damaged.", 400) from exc
    written = 0
    skipped = 0
    total = 0
    with handle:
        members = handle.getmembers()
        files = []
        for member in members:
            if member.isdir():
                continue
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                skipped += 1
                continue
            if not member.isfile():
                skipped += 1
                continue
            files.append(member)
            total += member.size
        if len(files) > EXTRACT_FILES or total > EXTRACT_BYTES:
            raise FilesError("That archive is too large.", 413)
        for member in files:
            target = member_dest(dest_dir, place, member.name)
            source = handle.extractfile(member)
            if source is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            remaining = member.size
            with source, target.open("wb") as out:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            written += 1
    return {"files": written, "skipped": skipped}


def zip_jobs(place: dict, rels: list[str]) -> list[tuple[Path, str]]:
    if not rels:
        raise FilesError("Choose a file first.")
    jobs: list[tuple[Path, str]] = []
    total_bytes = 0
    for rel in rels:
        text = rel_text(rel)
        if place["kind"] == "host" and text in ZIP_KEEP:
            raise FilesError("Choose a smaller folder.", 413)
        target = jail(place, rel)
        if not target.exists():
            raise FilesError("That file is gone.", 404)
        if target.is_file():
            jobs.append((target, target.name))
            total_bytes += target.stat().st_size
        elif target.is_dir():
            for current, _dirs, files in os.walk(target, followlinks=False):
                for filename in files:
                    file_path = Path(current) / filename
                    if file_path.is_symlink():
                        continue
                    try:
                        inside = file_path.relative_to(target)
                    except ValueError:
                        continue
                    jobs.append((file_path, f"{target.name}/{inside.as_posix()}"))
                    try:
                        total_bytes += file_path.stat().st_size
                    except OSError:
                        pass
                    if len(jobs) > ZIP_FILES or total_bytes > ZIP_BYTES:
                        raise FilesError("That folder is too large to download at once.", 413)
        if len(jobs) > ZIP_FILES or total_bytes > ZIP_BYTES:
            raise FilesError("That folder is too large to download at once.", 413)
    return jobs


def status_log() -> list[str]:
    if demo_root() is not None:
        return ["Demo mode does not mount disks."]
    lines = mounts.read_status().get("lines") or []
    return [str(line) for line in lines][-200:]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        if args and len(args) >= 2 and str(args[1]).startswith("2") and str(args[0]).startswith("GET /static/"):
            return
        print(f"root-files: {self.address_string()} {fmt % args}", flush=True)

    def parsed(self) -> tuple[str, dict[str, str]]:
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        return url.path, {key: values[-1] for key, values in query.items()}

    def ensure_client(self) -> None:
        if allow_all() or self.client_address[0] == INGRESS_IP:
            return
        self.close_connection = True
        self.send_json(403, {"error": "Open this from the Home Assistant sidebar."})
        raise StopHandling()

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, status: int, data: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            quoted = urllib.parse.quote(filename)
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quoted}")
        self.end_headers()
        self.wfile.write(data)

    def iter_body(self, limit: int):
        length = self.headers.get("Content-Length")
        if length is not None:
            try:
                remaining = int(length)
            except ValueError as exc:
                raise FilesError("That upload is not valid.", 400) from exc
            if remaining > limit:
                self.close_connection = True
                raise FilesError("That upload is too large.", 413)
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
            return
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            written = 0
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                try:
                    size = int(line.split(b";", 1)[0], 16)
                except ValueError as exc:
                    raise FilesError("That upload is not valid.", 400) from exc
                if size == 0:
                    self.rfile.readline()
                    break
                if written + size > limit:
                    self.close_connection = True
                    raise FilesError("That upload is too large.", 413)
                left = size
                while left:
                    chunk = self.rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    written += len(chunk)
                    yield chunk
                self.rfile.readline()
            return

    def read_json(self) -> dict:
        raw = b"".join(self.iter_body(2_000_000))
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FilesError("That request was not valid.", 400) from exc
        if not isinstance(data, dict):
            raise FilesError("That request was not valid.", 400)
        return data

    def do_GET(self) -> None:
        self._guard(self.route_get)

    def do_POST(self) -> None:
        self._guard(self.route_post)

    def _guard(self, route) -> None:
        try:
            self.ensure_client()
            route()
        except StopHandling:
            return
        except FilesError as exc:
            self._safe_error(exc.status, str(exc))
        except (BrokenPipeError, ConnectionError):
            self.close_connection = True
        except Exception as exc:
            print(f"root-files: {exc}", flush=True)
            self._safe_error(500, "Something went wrong opening that.")

    def _safe_error(self, status: int, message: str) -> None:
        self.close_connection = True
        try:
            self.send_json(status, {"error": message})
        except Exception:
            return

    def route_get(self) -> None:
        path, query = self.parsed()
        if path in {"/", "/index.html"}:
            self._static("index.html")
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/") :])
            return
        if path == "/api/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/api/places":
            self.send_json(200, {"places": [public_place(place) for place in all_places()], "log": status_log()})
            return
        if path == "/api/list":
            place = place_by_id(query.get("place", ""))
            self.send_json(200, list_dir(place, query.get("path", "")))
            return
        if path == "/api/search":
            place = place_by_id(query.get("place", ""))
            hidden = query.get("hidden", "1") != "0"
            self.send_json(
                200,
                {"entries": search(place, query.get("path", ""), query.get("q", ""), hidden)},
            )
            return
        if path == "/api/text":
            place = place_by_id(query.get("place", ""))
            data = read_text(place, query.get("path", ""))
            self.send_bytes(200, data, "text/plain; charset=utf-8")
            return
        if path == "/api/raw":
            place = place_by_id(query.get("place", ""))
            target = jail(place, query.get("path", ""))
            if not target.is_file():
                raise FilesError("That is not a file.", 400)
            self._send_file(target, download=query.get("download") == "1")
            return
        raise FilesError("Not found.", 404)

    def route_post(self) -> None:
        path, query = self.parsed()
        if path == "/api/upload":
            self._upload(query)
            return
        if path == "/api/text":
            place = place_by_id(query.get("place", ""))
            data = b"".join(self.iter_body(TEXT_LIMIT))
            write_text(place, query.get("path", ""), data)
            self.send_json(200, {"ok": True})
            return
        data = self.read_json()
        if path == "/api/mkdir":
            place = place_by_id(str(data.get("place", "")))
            name = make_dir(place, str(data.get("path", "")), str(data.get("name", "")))
            self.send_json(200, {"ok": True, "name": name})
            return
        if path == "/api/rename":
            place = place_by_id(str(data.get("place", "")))
            renamed = rename(place, str(data.get("path", "")), str(data.get("name", "")))
            self.send_json(200, {"ok": True, "path": renamed})
            return
        if path == "/api/delete":
            place = place_by_id(str(data.get("place", "")))
            paths = data.get("paths") or []
            if not isinstance(paths, list):
                raise FilesError("Choose a file first.")
            delete_paths(place, [str(item) for item in paths])
            self.send_json(200, {"ok": True})
            return
        if path == "/api/transfer":
            source = place_by_id(str(data.get("from_place", "")))
            dest = place_by_id(str(data.get("to_place", "")))
            paths = data.get("paths") or []
            if not isinstance(paths, list):
                raise FilesError("Choose a file first.")
            done = transfer(
                str(data.get("op", "")),
                source,
                [str(item) for item in paths],
                dest,
                str(data.get("to_path", "")),
            )
            self.send_json(200, {"ok": True, "count": done})
            return
        if path == "/api/extract":
            place = place_by_id(str(data.get("place", "")))
            result = extract_archive(place, str(data.get("path", "")))
            self.send_json(200, {"ok": True, **result})
            return
        if path == "/api/zip":
            place = place_by_id(str(data.get("place", "")))
            paths = data.get("paths") or []
            if not isinstance(paths, list):
                raise FilesError("Choose a file first.")
            self._send_zip(place, [str(item) for item in paths])
            return
        if path == "/api/rescan":
            self._rescan()
            return
        if path == "/api/eject":
            place = place_by_id(str(data.get("place", "")))
            if not place["ejectable"]:
                raise FilesError("That drive is managed by Home Assistant Storage.", 400)
            try:
                mounts.eject(place["path"])
            except (RuntimeError, ValueError, OSError) as exc:
                raise FilesError(str(exc), 409) from exc
            self.send_json(200, {"ok": True})
            return
        raise FilesError("Not found.", 404)

    def _static(self, rel: str) -> None:
        if not rel or ".." in rel.split("/"):
            raise FilesError("Not found.", 404)
        root = STATIC.resolve()
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise FilesError("Not found.", 404)
        if not target.is_file():
            raise FilesError("Not found.", 404)
        data = target.read_bytes()
        self.send_bytes(200, data, content_type(target))

    def _upload(self, query: dict[str, str]) -> None:
        place = place_by_id(query.get("place", ""))
        name = safe_segment(query.get("name", ""))
        directory = jail(place, query.get("path", ""))
        if not directory.is_dir():
            self.close_connection = True
            raise FilesError("That folder does not exist.", 404)
        target = jail(place, join_rel(query.get("path", ""), name))
        partial = target.with_name(target.name + ".uploading")
        try:
            with partial.open("wb") as handle:
                for chunk in self.iter_body(UPLOAD_LIMIT):
                    handle.write(chunk)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        self.send_json(200, {"ok": True, "name": name})

    def _send_file(self, target: Path, download: bool) -> None:
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise FilesError(exc.strerror or "Cannot read that file.", 403) from exc
        start, end, status = 0, max(size - 1, 0), 200
        range_header = self.headers.get("Range")
        if size and range_header and range_header.startswith("bytes=") and "," not in range_header:
            spec = range_header[6:]
            left, _, right = spec.partition("-")
            try:
                if left == "":
                    length = int(right or "0")
                    start = max(0, size - length)
                    end = size - 1
                else:
                    start = int(left)
                    end = int(right) if right else size - 1
            except ValueError:
                start, end = 0, size - 1
            else:
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206
        length = 0 if size == 0 else end - start + 1
        quoted = urllib.parse.quote(target.name)
        disposition = "attachment" if download else "inline"
        self.send_response(status)
        self.send_header("Content-Type", content_type(target))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quoted}")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if length == 0:
            return
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _send_zip(self, place: dict, rels: list[str]) -> None:
        jobs = zip_jobs(place, rels)
        # A temp file keeps the zip valid if the stream cannot seek.
        handle = Path("/tmp") / f"root-files-{threading.get_ident()}.zip"
        try:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for file_path, arcname in jobs:
                    archive.write(file_path, arcname)
            size = handle.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''files.zip")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with handle.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            handle.unlink(missing_ok=True)

    def _rescan(self) -> None:
        if demo_root() is not None:
            self.send_json(200, {"ok": True, "log": ["Demo mode does not mount disks."]})
            return
        with MOUNT_LOCK:
            status = mounts.mount_all()
        self.send_json(200, {"ok": True, "log": status.get("lines") or []})


def main() -> None:
    if demo_root() is None:
        try:
            mounts.mount_all()
        except Exception as exc:
            print(f"root-files: {exc}", flush=True)
    host = os.environ.get("FILES_HOST", "0.0.0.0")
    port = int(os.environ.get("FILES_PORT", "8099"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True

    def stop(_signum, _frame) -> None:
        print("root-files: stopping", flush=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"root-files: listening on {port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if demo_root() is None:
            try:
                mounts.unmount_all()
            except Exception as exc:
                print(f"root-files: unmount: {exc}", flush=True)


if __name__ == "__main__":
    main()
