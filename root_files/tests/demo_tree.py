"""Sample folders for local runs and tests. Not copied into the add-on image."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + (bytes(color) * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def build_demo(root: Path) -> None:
    packages = root / "config" / "packages"
    packages.mkdir(parents=True)
    (root / "config" / "configuration.yaml").write_text(
        "default_config:\n\nhomeassistant:\n  packages: !include_dir_named packages\n",
        encoding="utf-8",
    )
    (packages / "irrigation.yaml").write_text(
        "input_boolean:\n  irrigation_automatic:\n    name: Automatic\n",
        encoding="utf-8",
    )
    storage = root / "config" / ".storage"
    storage.mkdir()
    (storage / "auth").write_text("{}\n", encoding="utf-8")
    (root / "media" / "photos").mkdir(parents=True)
    (root / "share").mkdir()
    (root / "share" / "readme.txt").write_text("shared\n", encoding="utf-8")
    (root / "backup").mkdir()
    (root / "ssl").mkdir()
    (root / "addons").mkdir()
    (root / "addon_configs").mkdir()
    photos = root / "places" / "usb" / "POOL-CAM" / "photos"
    photos.mkdir(parents=True)
    (photos / "note.txt").write_text("pool camera\n", encoding="utf-8")
    (photos / "pool.png").write_bytes(_png(8, 8, (224, 160, 69, 255)))
    host = root / "places" / "host"
    (host / "etc").mkdir(parents=True)
    (host / "etc" / "hostname").write_text("homeassistant\n", encoding="utf-8")
    (host / "mnt" / "data").mkdir(parents=True)
    (host / "mnt" / "data" / "hello.txt").write_text("data disk\n", encoding="utf-8")
