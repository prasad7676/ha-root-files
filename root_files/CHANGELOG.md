# Changelog

## 1.0.9

- HA is a backup disk again, and new backups go there. A backup disk cannot also stay open in Files while Protection mode is on.

## 1.0.8

- A local disk set as a backup is shared as Media, so Files can open it while Protection mode stays on. New backups stay in Home Assistant.

## 1.0.7

- A Storage disk with no use is shared as Media, so it stays in Files while Protection mode is on. Host system files still need Protection mode off.

## 1.0.6

- Disks shared as Media or Share stay available with Protection mode on. Protection mode is only needed for system files.

## 1.0.5

- Opening a host Storage disk no longer rewrites its path into a missing folder.

## 1.0.4

- Attached disks are listed as Disks. USB, SATA, NVMe, and SD are handled the same way.

## 1.0.3

- Opening a Storage disk works when Home Assistant keeps it on the host.

## 1.0.2

- Disks added in Settings → System → Storage show up from Media, Share, and Backups.

## 1.0.1

- Folder listing works on the Python 3.11 image Home Assistant builds.

## 1.0.0

- Sidebar file manager for Config, media, share, backups, certificates, and add-ons.
- USB sticks (FAT, exFAT, NTFS, ext4) get their own place, with eject.
- Host shows the operating system when Protection mode is off.
- Edit text beside the list, upload by dropping files, and unpack zip and tar archives.
