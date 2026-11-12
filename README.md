# Root File Explorer

A Home Assistant add-on repository. It browses the usual folders, attached disks, and, with Protection mode off, the host system.

This is not the File Explorer from the built-in store. That one stops at the folders Home Assistant already shares.

## Add this repository

In Home Assistant: Settings → Apps → App store → the three dots → Repositories. Add:

```
https://github.com/prasad7676/ha-root-files
```

Open Root File Explorer and install it. The machine builds the image, so leave a couple of GB free. The first install can take several minutes.

Turn on Advanced mode in your profile if the Protection mode switch is missing. Turn Protection mode off, start the app, and show it in the sidebar. The entry is named Files.

Host and extra disks stay unavailable until Protection mode is off. FAT, exFAT, NTFS, and ext4 disks can be mounted, whether they are USB, SATA, NVMe, or SD. Mac APFS disks cannot. Format a disk as exFAT if it should open on both a Mac and Home Assistant.

Host is the operating system. Deleting a file in the wrong system folder can stop Home Assistant from booting. Day-to-day YAML belongs under Config.
