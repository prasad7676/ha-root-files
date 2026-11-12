import unittest

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import mounts


class MountPlanTest(unittest.TestCase):
    def test_skips_home_assistant_disk_and_names_usb(self):
        system = {
            "name": "mmcblk0",
            "path": "/dev/mmcblk0",
            "type": "disk",
            "children": [
                {"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "type": "part", "fstype": "vfat", "label": "hassos-boot"},
                {"name": "mmcblk0p8", "path": "/dev/mmcblk0p8", "type": "part", "fstype": "ext4", "label": "hassos-data"},
            ],
        }
        usb = {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "tran": "usb",
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "exfat", "label": "POOL CAM", "size": 100},
            ],
        }
        plans, notes = mounts.plan_usb_mounts([system, usb], mounted=set())
        self.assertEqual([plan.source for plan in plans], ["/dev/sda1"])
        self.assertEqual(plans[0].target_name, "POOL-CAM")
        self.assertTrue(any("system disk" in note for note in notes))

    def test_skips_disk_already_mounted_on_the_host(self):
        disk = {
            "name": "sdb",
            "path": "/dev/sdb",
            "type": "disk",
            "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "ext4", "label": "extra"},
            ],
        }
        plans, notes = mounts.plan_usb_mounts([disk], mounted={"/dev/sdb1"})
        self.assertEqual(plans, [])
        self.assertTrue(any("already mounted" in note for note in notes))

    def test_mounts_a_stick_with_no_partition_table(self):
        disk = {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "fstype": "vfat",
            "label": "",
            "children": [],
        }
        plans, _notes = mounts.plan_usb_mounts([disk], mounted=set())
        self.assertEqual(plans[0].source, "/dev/sda")
        self.assertEqual(plans[0].target_name, "sda")

    def test_rejects_mac_filesystems(self):
        disk = {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "apfs", "label": "Macintosh"},
            ],
        }
        plans, notes = mounts.plan_usb_mounts([disk], mounted=set())
        self.assertEqual(plans, [])
        self.assertTrue(any("not supported" in note for note in notes))

    def test_unescape_mount_spaces(self):
        self.assertEqual(mounts.unescape_mount(r"/media/My\040Disk"), "/media/My Disk")

    def test_media_mounts_are_one_level_deep(self):
        lines = [
            "/dev/sda1 /media/POOL\\040CAM exfat rw 0 0",
            "overlay /media/POOL\\040CAM/nested ext4 rw 0 0",
            "/dev/sda1 /mnt/data ext4 rw 0 0",
        ]
        found = mounts.media_mounts(lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["path"], "/media/POOL CAM")
        self.assertEqual(found[0]["fstype"], "exfat")


if __name__ == "__main__":
    unittest.main()
