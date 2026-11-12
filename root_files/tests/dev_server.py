"""Run the file manager against sample folders on this Mac."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_tree import build_demo
import server


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="root-files-"))
    build_demo(root)
    os.environ["FILES_DEMO"] = str(root)
    os.environ["FILES_ALLOW_ALL"] = "1"
    os.environ["FILES_HOST"] = "127.0.0.1"
    os.environ["FILES_PORT"] = os.environ.get("FILES_PORT", "8765")
    print(f"demo {root}", flush=True)
    print(f"http://127.0.0.1:{os.environ['FILES_PORT']}/", flush=True)
    server.main()


if __name__ == "__main__":
    main()
