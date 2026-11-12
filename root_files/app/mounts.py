"""Mount the host root and extra disks for the file explorer.

Home Assistant's own folders are already mounted by the Supervisor.
The host root is reached through process 1 when Protection mode is off
and this add-on shares the host process namespace. Attached disks are
mounted here only when they are not the Home Assistant system disk.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

ALLOWED_FS = {"ext2", "ext3", "ext4", "vfat", "exfat", "ntfs", "ntfs3", "msdos"}
DISK_ROOT = Path("/places/disks")
HOST_ROOT = Path("/places/host")
STORAGE_ROOT = Path("/places/storage")
STATUS_PATHS = (Path("/data/mount-status.json"), Path("/tmp/root-files-mount-status.json"))


@dataclass
class DiskPlan:
    source: str
    label: str
    fstype: str
    size: int
    target_name: str


def sanitize_name(value: str) -> str:
    cleaned: list[str] = []
    for char in (value or "").strip():
        if char.isalnum() or char in "._-":
            cleaned.append(char)
        else:
            cleaned.append("-")
    name = "".join(cleaned).strip(".-")
    while "--" in name:
        name = name.replace("--", "-")
    return name[:40]


def unescape_mount(value: str) -> str:
    """Decode the octal escapes used in /proc/mounts, such as \\040 for a space."""
    if "\\" not in value:
        return value
    try:
        return value.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeError, ValueError):
        return value


def disk_is_system(node: dict) -> bool:
    labels: list[str] = []

    def walk(item: dict) -> None:
        label = (item.get("label") or "").lower()
        if label:
            labels.append(label)
        for child in item.get("children") or []:
            walk(child)

    walk(node)
    return any(label.startswith("hassos") for label in labels)


def plan_disk_mounts(disks: list[dict], mounted: set[str]) -> tuple[list[DiskPlan], list[str]]:
    """Decide which attached disks to mount. USB, SATA, NVMe, and SD are treated the same.

    The Home Assistant system disk is never included.
    """
    plans: list[DiskPlan] = []
    notes: list[str] = []
    used: set[str] = set()

    def unique(base: str) -> str:
        name = sanitize_name(base) or "disk"
        if name not in used:
            used.add(name)
            return name
        index = 2
        while f"{name}-{index}" in used:
            index += 1
        used.add(f"{name}-{index}")
        return f"{name}-{index}"

    def consider(node: dict) -> None:
        children = node.get("children") or []
        if node.get("type") == "disk" and disk_is_system(node):
            label = node.get("label") or node.get("name") or node.get("path") or "disk"
            notes.append(f"Skipping {label}: it is the Home Assistant system disk.")
            return
        fstype = (node.get("fstype") or "").lower()
        path = node.get("path") or ""
        label = node.get("label") or ""
        kind = node.get("type")
        whole = kind == "disk" and not children and fstype in ALLOWED_FS and path
        part = kind == "part" and fstype in ALLOWED_FS and path
        if whole or part:
            if label.lower().startswith("hassos"):
                notes.append(f"Skipping {path}: it is a Home Assistant system partition.")
            elif path in mounted:
                notes.append(f"Skipping {label or path}: it is already mounted.")
            else:
                plans.append(
                    DiskPlan(
                        source=path,
                        label=label,
                        fstype=fstype,
                        size=int(node.get("size") or 0),
                        target_name=unique(label or Path(path).name),
                    )
                )
            return
        if kind == "part" and path and fstype and fstype not in ALLOWED_FS:
            notes.append(f"Skipping {label or path}: {fstype} is not supported.")
        for child in children:
            consider(child)

    for disk in disks:
        consider(disk)
    return plans, notes


def mount_commands(plan: DiskPlan, target: str) -> list[list[str]]:
    common = "rw,nosuid,nodev,noexec"
    if plan.fstype in {"vfat", "msdos"}:
        fat = common + ",utf8,uid=0,gid=0,umask=000"
        return [["mount", "-t", "vfat", "-o", fat, plan.source, target]]
    if plan.fstype == "exfat":
        return [
            ["mount", "-t", "exfat", "-o", common, plan.source, target],
            ["mount", "-t", "exfat-fuse", "-o", common, plan.source, target],
        ]
    if plan.fstype in {"ntfs", "ntfs3"}:
        return [
            ["mount", "-t", "ntfs3", "-o", common, plan.source, target],
            ["mount", "-t", "ntfs-3g", "-o", common, plan.source, target],
        ]
    return [["mount", "-t", plan.fstype, "-o", common, plan.source, target]]


def is_mountpoint(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        result = subprocess.run(
            ["mountpoint", "-q", str(path)],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        return path.stat().st_dev != path.parent.stat().st_dev
    except OSError:
        return False


def mounted_sources() -> set[str]:
    """Device paths already mounted in this container or on the host.

    /proc/1/mounts is the host table when Protection mode is off, because
    process 1 is then the machine's init. That keeps us from mounting the
    Home Assistant data disk a second time.
    """
    found: set[str] = set()
    for path in (Path("/proc/mounts"), Path("/proc/1/mounts")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split()
            if parts:
                found.add(unescape_mount(parts[0]))
    return found


# Home Assistant puts a Storage disk one folder down from these.
# Backup disks that are not shared with apps stay on the host under mounts/.
_CONTAINER_ROOTS = ("/media/", "/share/", "/backup/")
_HOST_ROOTS = {
    "/mnt/data/supervisor/media/": "/media/",
    "/mnt/data/supervisor/share/": "/share/",
    "/mnt/data/supervisor/backup/": "/backup/",
    "/mnt/data/supervisor/mounts/": "/mnt/data/supervisor/mounts/",
}


def _mount_fields(line: str) -> tuple[str, str, str] | None:
    """Return source, target, fstype from a /proc/mounts or mountinfo line."""
    if " - " in line:
        left, right = line.split(" - ", 1)
        left_parts = left.split()
        right_parts = right.split()
        if len(left_parts) < 5 or len(right_parts) < 2:
            return None
        return unescape_mount(right_parts[1]), unescape_mount(left_parts[4]), right_parts[0]
    parts = line.split()
    if len(parts) < 3:
        return None
    return unescape_mount(parts[0]), unescape_mount(parts[1]), parts[2]


def _storage_target(target: str) -> str | None:
    """Map a host or container mount path to the folder the app should open."""
    target = target.rstrip("/") or "/"
    for prefix in _CONTAINER_ROOTS:
        if target.startswith(prefix) and target.count("/") == 2:
            return target
    for host_prefix, container_prefix in _HOST_ROOTS.items():
        if target.startswith(host_prefix) and target.count("/") == host_prefix.count("/"):
            return container_prefix + target[len(host_prefix):]
    return None


def storage_from_lines(lines: list[str]) -> list[dict]:
    """Storage disks already mounted by Home Assistant, one entry per folder."""
    found: dict[str, dict] = {}
    for line in lines:
        fields = _mount_fields(line)
        if fields is None:
            continue
        source, target, fstype = fields
        path = _storage_target(target)
        if path is None:
            continue
        current = found.get(path)
        # An autofs trigger sits on the folder until the disk is opened.
        # Prefer the real filesystem when both lines are present.
        if current is not None and current["fstype"] != "autofs":
            continue
        if current is not None and fstype == "autofs":
            continue
        found[path] = {
            "source": source,
            "path": path,
            "fstype": fstype,
            "name": Path(path).name,
        }
    return list(found.values())


def _read_mount_lines() -> list[str]:
    lines: list[str] = []
    for path in (Path("/proc/self/mountinfo"), Path("/proc/mounts"), Path("/proc/1/mountinfo"), Path("/proc/1/mounts")):
        try:
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return lines


def expose_storage(item: dict) -> dict:
    """Bind a host-only Storage disk into this container so the file list can open it.

    /proc/1/root is a magic symlink. stat() can see through it, but
    resolving the path turns it into /mnt/data/... which does not exist
    in the add-on.
    """
    text = item["path"]
    if not text.startswith("/proc/1/root/"):
        return item
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    target = STORAGE_ROOT / (sanitize_name(item["name"]) or "disk")
    target.mkdir(parents=True, exist_ok=True)
    if not is_mountpoint(target):
        error = _run_mount([
            ["mount", "--no-canonicalize", "--bind", text, str(target)],
            ["mount", "-c", "--bind", text, str(target)],
        ])
        if error or not is_mountpoint(target):
            return {**item, "mount_error": error or "The disk could not be attached inside the add-on."}
    return {**item, "path": str(target)}


def _dir_device(path: Path) -> int | None:
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    return info.st_dev


def storage_mounts(lines: list[str] | None = None) -> list[dict]:
    """Disks from Settings → System → Storage.

    Passed lines are parsed and returned as-is, for tests. Live discovery
    also stats each folder under /media, /share, and /backup, because
    Home Assistant leaves an autofs trigger there and the disk does not
    appear in the mount table until that folder is opened.
    """
    if lines is not None:
        return storage_from_lines(lines)

    found = {item["path"]: item for item in storage_from_lines(_read_mount_lines())}
    for root in ("/media", "/share", "/backup"):
        root_path = Path(root)
        parent_dev = _dir_device(root_path)
        if parent_dev is None:
            continue
        try:
            children = list(root_path.iterdir())
        except OSError:
            continue
        for child in children:
            child_dev = _dir_device(child)
            if child_dev is None or child_dev == parent_dev:
                continue
            path = str(child)
            found.setdefault(
                path,
                {"source": "", "path": path, "fstype": "", "name": child.name},
            )
    # Statting the folder wakes the disk, so read the table again for exFAT/NTFS.
    for item in storage_from_lines(_read_mount_lines()):
        current = found.get(item["path"])
        if current is None:
            found[item["path"]] = item
        elif item["fstype"] and item["fstype"] != "autofs":
            current["fstype"] = item["fstype"]
            current["source"] = item["source"] or current["source"]

    visible: list[dict] = []
    for item in found.values():
        path = Path(item["path"])
        if _dir_device(path) is None:
            path = Path("/proc/1/root") / item["path"].lstrip("/")
            if _dir_device(path) is None:
                continue
            item = {**item, "path": str(path)}
        visible.append(expose_storage(item))
    return visible


def read_options() -> dict:
    path = Path("/data/options.json")
    if not path.is_file():
        return {"show_host": True, "mount_usb": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"show_host": True, "mount_usb": True}
    return {
        "show_host": bool(data.get("show_host", True)),
        "mount_usb": bool(data.get("mount_usb", True)),
    }


def read_status() -> dict:
    for path in STATUS_PATHS:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
    return {"host_ok": False, "host_message": "", "lines": []}


def _write_status(status: dict) -> None:
    raw = json.dumps(status, indent=2).encode("utf-8")
    for path in STATUS_PATHS:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return
        except OSError:
            continue


def _run_mount(commands: list[list[str]]) -> str:
    last = "mount failed"
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last = str(exc)
            continue
        if result.returncode == 0:
            return ""
        last = (result.stderr or result.stdout or "mount failed").strip()
    return last


def mount_host(show: bool) -> tuple[bool, str]:
    HOST_ROOT.mkdir(parents=True, exist_ok=True)
    if not show:
        if is_mountpoint(HOST_ROOT):
            subprocess.run(["umount", str(HOST_ROOT)], capture_output=True, timeout=20)
        return False, "Host root is turned off in the add-on options."
    if is_mountpoint(HOST_ROOT):
        return True, "Host root is already mounted."
    comm = ""
    try:
        comm = Path("/proc/1/comm").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if comm not in {"systemd", "init"}:
        who = comm or "unknown"
        return False, (
            "Host root is hidden because Protection mode is still on. "
            "On the add-on page, turn Protection mode off, then restart. "
            f"Process 1 is {who}."
        )
    error = _run_mount([["mount", "--bind", "/proc/1/root", str(HOST_ROOT)]])
    if error:
        return False, f"Could not mount the host root: {error}"
    return True, "Host root mounted."


def mount_all() -> dict:
    options = read_options()
    lines: list[str] = []
    host_ok, host_message = mount_host(options["show_host"])
    lines.append(host_message)
    if options["mount_usb"]:
        try:
            raw = subprocess.check_output(
                ["lsblk", "-J", "-b", "-o", "PATH,TYPE,FSTYPE,LABEL,TRAN,RM,SIZE"],
                text=True,
                timeout=20,
            )
            disks = json.loads(raw or "{}").get("blockdevices") or []
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            disks = []
            lines.append(f"Could not list disks: {exc}")
        plans, notes = plan_disk_mounts(disks, mounted_sources())
        lines.extend(notes)
        DISK_ROOT.mkdir(parents=True, exist_ok=True)
        for plan in plans:
            target = DISK_ROOT / plan.target_name
            target.mkdir(parents=True, exist_ok=True)
            error = _run_mount(mount_commands(plan, str(target)))
            if error:
                lines.append(f"Could not mount {plan.label or plan.source}: {error}")
                try:
                    target.rmdir()
                except OSError:
                    pass
            else:
                pretty = plan.label or plan.source
                lines.append(f"Mounted {pretty} ({plan.fstype}) at {target}.")
        if not plans and not any(line.startswith("Could not list") for line in lines):
            lines.append("No extra disk needed mounting.")
        for item in storage_mounts():
            kind = item["fstype"] or "mounted"
            problem = item.get("mount_error")
            suffix = f". {problem}" if problem else "."
            lines.append(f"Storage {item['name']} is available at {item['path']} ({kind}){suffix}")
    else:
        lines.append("Attached disks are not mounted. Turn that option on, then restart.")
    status = {"host_ok": host_ok, "host_message": host_message, "lines": lines[-200:]}
    _write_status(status)
    for line in lines:
        print(f"root-files: {line}", flush=True)
    return status


def unmount_all() -> None:
    targets: list[Path] = []
    if DISK_ROOT.is_dir():
        targets.extend(sorted((path for path in DISK_ROOT.iterdir() if path.is_dir()), reverse=True))
    if STORAGE_ROOT.is_dir():
        targets.extend(sorted((path for path in STORAGE_ROOT.iterdir() if path.is_dir()), reverse=True))
    targets.append(HOST_ROOT)
    for target in targets:
        if not is_mountpoint(target):
            continue
        subprocess.run(["umount", str(target)], capture_output=True, timeout=20)
        if is_mountpoint(target):
            subprocess.run(["umount", "-l", str(target)], capture_output=True, timeout=20)


def eject(target: Path) -> None:
    if not str(target).startswith(str(DISK_ROOT) + os.sep):
        raise ValueError("Only disks mounted by this add-on can be ejected.")
    if not is_mountpoint(target):
        raise ValueError("That drive is not mounted.")
    result = subprocess.run(
        ["umount", str(target)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        message = (result.stderr or "That drive is busy. Close any open files and try again.").strip()
        raise RuntimeError(message)
    try:
        target.rmdir()
    except OSError:
        pass
