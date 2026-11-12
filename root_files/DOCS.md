# Root File Explorer

A sidebar file manager for this Home Assistant. It shows the usual folders, attached disks, and, with Protection mode off, the host system.

The screen is our own. Places stay in a sidebar, the folder you are in stays in the breadcrumb, and a file opens beside the list so a YAML edit does not take over the page.

## Install

The add-on is built on the Home Assistant machine. Leave a couple of GB free. The first install downloads a small Linux image.

1. Settings → Apps → App store → the three dots → Repositories.
2. Add `https://github.com/prasad7676/ha-root-files` and open Root File Explorer.
3. Install it. The build can take several minutes.
4. If you do not see a Protection mode switch, open your profile (bottom left) and turn on Advanced mode, then come back.
5. Leave Protection mode on. Config, Media, Share, Backups, and disks set to Media or Share in Storage work with it on.
6. Start it. On the app page, turn on Show in sidebar. The entry is named Files.

Turn Protection mode off only when you need Host, the machine's own files. Turn on Show the host system in the add-on configuration, restart, use Host, then turn Protection mode back on.

To install without GitHub, copy the `root_files` folder onto the Samba `addons` share so `config.yaml` is at `addons/root_files/config.yaml`, then Check for updates and install the local app.

Config, media, share, backups, certificates, and add-ons are there with Protection mode on. A disk you set to Media or Share under Settings → System → Storage is there too. A disk that is only mounted on the host, and the Host place itself, need Protection mode off.

## Options

Show the host system is off unless you turn it on. It mounts the machine's own files under Host and needs Protection mode off while you use it.

Mount attached disks mounts USB, SATA, NVMe, and SD disks that Home Assistant is not already using. That mount uses hardware access, so a brand-new disk still needs Protection mode off until Home Assistant is sharing it. FAT, exFAT, NTFS, and ext4 work. A drive formatted for the Mac as APFS or Mac OS Extended does not. Format that disk as exFAT if it should open on both.

A disk you already added under Settings → System → Storage as Media or Share shows up under Disks with Protection mode on. Eject applies to a disk this app mounted. Disks added in Storage are removed from that Storage page.

## Careful on Host

Host is the operating system. Deleting `etc`, `usr`, `boot`, or the data disk from the top of that list is refused. Files inside those folders can still be changed, and the wrong one can stop Home Assistant from booting. Day to day YAML belongs under Config.

The page is only in Home Assistant, and only for administrators. It does not open a port on the network.
