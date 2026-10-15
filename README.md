# FATXplorer-Linux

A Linux GTK 4 app for mounting an attached Xbox 360 FATX/XTAF volume and opening it in the desktop file manager. It scans SATA and removable USB drives, starts the FUSE mount, and provides a safe unmount button.

## Requirements

- Python 3, GTK 4, and Python GObject bindings
- `lsblk` (from util-linux)
- `fatx360fs`, a Linux FUSE driver for Xbox 360 FATX, available at [henriqueclaranhan/fatx360fs](https://github.com/henriqueclaranhan/fatx360fs)
- `fusermount3` (or `fusermount`) for unmounting
- For internal SATA HDD layouts: `pkexec`, `losetup`, and `setfacl` (`polkit`, `util-linux`, and `acl` on Arch)

Install the FUSE driver and its prerequisites using its project instructions. Then run:

```sh
python3 xbox_drive_manager.py
```

Connect the HDD using a USB SATA adapter/enclosure or directly through a SATA connection, click **Refresh**, select the Xbox volume, and click **Mount**. SATA disks and USB disks are listed with their connection type. For retail Xbox 360 SATA HDD layouts, the app checks documented volume offsets and offers only offsets where it finds a FATX/XTAF signature; it creates a temporary loop device for the selected volume after desktop authentication. This does not edit the disk's partition table. Mounted disks are excluded from the selectable list; still verify the device model and size before mounting. Once mounted, click **Open mounted drive** to browse it in the desktop file manager. Always click **Unmount** and wait for success before disconnecting.

## Limits

The GUI depends on an external, early-stage FUSE driver; it does not implement the Xbox 360 disk format itself. The driver README documents writable FUSE mounting of a FATX block device, but Xbox 360 internal disks can have multiple partition layouts. A device or partition the driver cannot parse will not mount. It does not format disks or unlock encrypted storage.

The originally suggested [XboxDev/fatx](https://github.com/XboxDev/fatx) library targets the original Xbox filesystem, so this app uses the Xbox 360-specific `fatx360fs` driver instead.
