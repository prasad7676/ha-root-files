# Root File Explorer

A sidebar file manager for this Home Assistant. It shows the usual folders, USB drives, and, with Protection mode off, the host system.

The screen is our own. Places stay in a sidebar, the folder you are in stays in the breadcrumb, and a file opens beside the list so a YAML edit does not take over the page.

## Install

The add-on is built on the Home Assistant machine. Leave a couple of GB free. The first install downloads a small Linux image.

1. Settings → Apps → App store → the three dots → Repositories.
2. Add `https://github.com/prasad7676/ha-root-files` and open Root File Explorer.
3. Install it. The build can take several minutes.
4. If you do not see a Protection mode switch, open your profile (bottom left) and turn on Advanced mode, then come back.
5. Turn Protection mode off. Root and USB stay hidden until you do this, and the app will not start while the switch is on.
6. Start it. On the app page, turn on Show in sidebar. The entry is named Files.

To install without GitHub, copy the `root_files` folder onto the Samba `addons` share so `config.yaml` is at `addons/root_files/config.yaml`, then Check for updates and install the local app.

Config, media, share, backups, certificates, and add-ons are there either way. Host and USB need the switch off and a restart.

## Options

Show the host system mounts the machine's own files under Host.

Mount USB drives mounts sticks that Home Assistant is not already using. FAT, exFAT, NTFS, and ext4 work. A drive formatted for the Mac as APFS or Mac OS Extended does not. Format that stick as exFAT if it should open on both.

A disk you already added under Settings → System → Storage shows up under USB as well as Media. Eject on a drive this app mounted, then unplug it. Disks added in Storage are ejected from that Storage page.

## Careful on Host

Host is the operating system. Deleting `etc`, `usr`, `boot`, or the data disk from the top of that list is refused. Files inside those folders can still be changed, and the wrong one can stop Home Assistant from booting. Day to day YAML belongs under Config.

The page is only in Home Assistant, and only for administrators. It does not open a port on the network.
