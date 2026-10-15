#!/usr/bin/env python3
"""A small GTK front end for mounting and browsing Xbox 360 storage."""

import os
import json
import shutil
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk


# Retail Xbox 360 HDD volume positions, in bytes. These are candidates only:
# the app verifies the FATX/XTAF signature at each offset before offering one.
XBOX360_HDD_VOLUMES = (
    (0x10C080000, 0xCE30000, "System Extended"),
    (0x118EB0000, 0x8000000, "System Extended 2"),
    (0x120EB0000, 0x10000000, "Original Xbox compatibility"),
    (0x130EB0000, None, "Data"),
)


class DriveManager(Gtk.Application):
    def __init__(self):
        # Launch this process directly even if an older copy with the same
        # application ID is still registered on the desktop session.
        super().__init__(application_id="io.github.vinicius.XboxDriveManager",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.devices = []
        self.mount_process = None
        self.loop_device = None
        self.window = None
        self.mountpoint = Path.home() / ".local/share/xbox-drive-manager/mount"
        self.driver_log = self.mountpoint.parent / "fatx360fs.log"

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        window = Gtk.ApplicationWindow(application=self, title="Xbox Drive Manager")
        self.window = window
        window.set_default_size(760, 380)
        window.set_resizable(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        root.set_margin_top(28)
        root.set_margin_bottom(24)
        root.set_margin_start(30)
        root.set_margin_end(30)
        window.set_child(root)

        heading = Gtk.Label(label="Xbox Drive Manager")
        heading.set_xalign(0)
        heading.add_css_class("title-1")
        root.append(heading)
        intro = Gtk.Label(label="Mount an Xbox drive and browse its files.")
        intro.set_xalign(0)
        intro.add_css_class("dim-label")
        root.append(intro)

        drive_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.append(drive_row)
        drive_name = Gtk.Label(label="Xbox HDD")
        drive_name.set_width_chars(12)
        drive_name.set_xalign(0)
        drive_row.append(drive_name)
        self.device_model = Gtk.StringList.new(["No drives scanned"])
        self.device_picker = Gtk.DropDown(model=self.device_model)
        self.device_picker.set_hexpand(True)
        drive_row.append(self.device_picker)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self.refresh_devices)
        drive_row.append(refresh)
        self.mount_button = Gtk.Button(label="Mount")
        self.mount_button.add_css_class("suggested-action")
        self.mount_button.connect("clicked", self.mount_drive)
        drive_row.append(self.mount_button)
        self.unmount_button = Gtk.Button(label="Unmount")
        self.unmount_button.set_sensitive(False)
        self.unmount_button.connect("clicked", self.unmount_drive)
        drive_row.append(self.unmount_button)
        info = Gtk.Label(label=("Mounts an Xbox 360 XTAF/FATX volume through the optional fatx360fs FUSE driver. "
                                "This does not format or unlock a drive. Disconnect it only after unmounting."))
        info.set_wrap(True)
        info.set_xalign(0)
        info.add_css_class("card")
        root.append(info)
        open_drive = Gtk.Button(label="Open mounted drive")
        open_drive.set_halign(Gtk.Align.START)
        open_drive.set_sensitive(False)
        self.open_button = open_drive
        open_drive.connect("clicked", self.open_drive)
        root.append(open_drive)

        self.status = Gtk.Label(label="Select the Xbox drive, then click Mount.")
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        root.append(self.status)
        self.refresh_devices()

        window.present()

    def refresh_devices(self, *_):
        try:
            self._volume_scan_permission_error = False
            result = subprocess.run(
                ["lsblk", "--json", "--bytes", "-o", "PATH,NAME,SIZE,TYPE,MODEL,TRAN,RM,FSTYPE,MOUNTPOINT"],
                check=True, capture_output=True, text=True,
            )
            data = json.loads(result.stdout)
            entries = []
            def visit(device, parent_external=False, parent_transport=""):
                # Xbox disks may be connected directly over SATA or through USB.
                # Avoid offering a whole disk when one of its partitions is mounted
                # (for example, the computer's system disk).
                transport = str(device.get("tran") or parent_transport or "").lower()
                external = (parent_external or transport in ("usb", "sata")
                            or bool(device.get("rm")))
                children = device.get("children", [])
                def has_mounted_descendant(node):
                    return any(child.get("mountpoint") or has_mounted_descendant(child)
                               for child in node.get("children", []))
                mounted_child = has_mounted_descendant(device)
                path = device.get("path")
                is_sata_disk = transport == "sata" and device.get("type") == "disk"
                found_offsets = []
                signature_at_zero = False
                if is_sata_disk and path and not device.get("mountpoint") and not mounted_child:
                    signature_at_zero, found_offsets = self._xbox360_volumes(
                        path, int(device.get("size") or 0))
                if external and device.get("type") in ("disk", "part"):
                    can_offer_disk = not is_sata_disk or signature_at_zero
                    if (path and can_offer_disk and not device.get("mountpoint")
                            and not (device.get("type") == "disk" and mounted_child)):
                        label = " ".join(x for x in [path, transport.upper() or "removable",
                                                       device.get("model"), device.get("fstype"),
                                                       self._size(int(device.get("size") or 0))] if x)
                        entries.append((path, label, None, None, None))
                for offset, length, volume_name in found_offsets:
                    entries.append((path,
                                    f"{path} · FATX {volume_name} (offset 0x{offset:X}, {self._size(length)})",
                                    None, offset, length))
                for child in children:
                    visit(child, external, transport)
            for device in data.get("blockdevices", []):
                visit(device)
            self.devices = entries
            labels = [f"{label} (already mounted)" if mount else label
                      for _, label, mount, _, _ in entries]
            if not labels:
                if self._volume_scan_permission_error:
                    labels = ["Cannot inspect SATA disks: permission denied"]
                    self.status.set_text("Allow your user read access to the Xbox disk, then click Refresh.")
                else:
                    labels = ["No compatible FATX volumes found"]
            self.device_model.splice(0, self.device_model.get_n_items(), labels)
            data_volume = next((index for index, label in enumerate(labels)
                                if "FATX Data " in label), 0)
            self.device_picker.set_selected(data_volume)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            self.status.set_text(f"Could not scan drives: {error}")

    @staticmethod
    def _size(value):
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}"
            size /= 1024

    @staticmethod
    def _xbox360_volumes(device, device_size):
        """Return retail HDD FATX volumes whose signatures are present on disk."""
        found = []
        signature_at_zero = False
        try:
            with open(device, "rb", buffering=0) as raw:
                signature_at_zero = raw.read(4) in (b"XTAF", b"FATX")
                for offset, nominal_size, name in XBOX360_HDD_VOLUMES:
                    if offset + 4 > device_size:
                        continue
                    raw.seek(offset)
                    if raw.read(4) not in (b"XTAF", b"FATX"):
                        continue
                    length = nominal_size if nominal_size is not None else device_size - offset
                    length = min(length, device_size - offset)
                    if length > 0:
                        found.append((offset, length, name))
        except PermissionError:
            self._volume_scan_permission_error = True
        except OSError:
            pass
        return signature_at_zero, found

    def mount_drive(self, *_):
        if self.mount_process and self.mount_process.poll() is None:
            self.status.set_text(f"Drive mounted at {self.mountpoint}")
            return
        index = self.device_picker.get_selected()
        if index >= len(self.devices):
            self.status.set_text("Connect the Xbox drive and refresh the list.")
            return
        device, label, mount, offset, length = self.devices[index]
        if mount:
            self.status.set_text(f"{device} is already mounted at {mount}; unmount it from the file manager first.")
            return
        driver = shutil.which("fatx360fs")
        if not driver:
            self.status.set_text("Install the fatx360fs FUSE driver first; see README for the project link.")
            return
        try:
            self.mountpoint.mkdir(parents=True, exist_ok=True)
            mount_device = device
            if offset is not None:
                mount_device = self._create_offset_device(device, offset, length)
            with self.driver_log.open("w", encoding="utf-8") as log:
                self.mount_process = subprocess.Popen(
                    [driver, mount_device, str(self.mountpoint)],
                    stdout=log, stderr=subprocess.STDOUT,
                )
            self.mount_button.set_sensitive(False)
            threading.Thread(target=self._wait_for_mount, daemon=True).start()
        except OSError as error:
            self._release_offset_device()
            self.status.set_text(f"Could not start FATX mount: {error}")
        except RuntimeError as error:
            self._release_offset_device()
            self.status.set_text(str(error))

    def _create_offset_device(self, backing_device, offset, size):
        pkexec = shutil.which("pkexec")
        losetup = shutil.which("losetup")
        setfacl = shutil.which("setfacl")
        if not pkexec or not losetup or not setfacl:
            raise RuntimeError("Offset mounting needs pkexec, losetup, and setfacl (Arch packages: polkit, util-linux, acl).")
        result = subprocess.run(
            [pkexec, losetup, "--find", "--show", "--offset", str(offset),
             "--sizelimit", str(size), backing_device],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f"Could not create the Xbox partition device: {result.stderr.strip() or result.stdout.strip()}")
        loop_device = result.stdout.strip().splitlines()[-1]
        permission = subprocess.run(
            [pkexec, setfacl, "-m", f"u:{os.getuid()}:rw", loop_device],
            capture_output=True, text=True,
        )
        if permission.returncode:
            subprocess.run([pkexec, losetup, "--detach", loop_device], capture_output=True, text=True)
            raise RuntimeError(f"Could not grant this session access to {loop_device}: {permission.stderr.strip()}")
        self.loop_device = loop_device
        return loop_device

    def _release_offset_device(self):
        if not self.loop_device:
            return None
        pkexec = shutil.which("pkexec")
        losetup = shutil.which("losetup")
        if not pkexec or not losetup:
            return "Could not detach the temporary loop device; run sudo losetup -d " + self.loop_device
        result = subprocess.run([pkexec, losetup, "--detach", self.loop_device],
                                capture_output=True, text=True)
        if result.returncode:
            return f"Could not detach {self.loop_device}: {result.stderr.strip()}"
        self.loop_device = None
        return None

    def _wait_for_mount(self):
        process = self.mount_process
        for _ in range(50):
            if process.poll() is not None:
                GLib.idle_add(self._mount_failed, "Could not mount this volume. Check the driver output and confirm the selected device contains a supported FATX/XTAF volume.")
                return
            if os.path.ismount(self.mountpoint):
                GLib.idle_add(self._mount_succeeded)
                return
            threading.Event().wait(0.1)
        GLib.idle_add(self._mount_failed, "Mount did not become available. Check that this is a supported FATX/XTAF volume.")

    def _mount_succeeded(self):
        self.mount_button.set_sensitive(False)
        self.unmount_button.set_sensitive(True)
        self.open_button.set_sensitive(True)
        self.status.set_text(f"Mounted at {self.mountpoint}. Opening it in the file manager…")
        self.open_drive()

    def _mount_failed(self, message):
        if self.mount_process and self.mount_process.poll() is None:
            self.mount_process.terminate()
            try:
                self.mount_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        self.mount_button.set_sensitive(True)
        self.open_button.set_sensitive(False)
        try:
            details = self.driver_log.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            details = ""
        cleanup_error = self._release_offset_device()
        if details:
            message = f"{message}\nDriver: {details[-700:]}"
        else:
            message = f"{message}\nNo details from the driver. Log: {self.driver_log}"
        if cleanup_error:
            message += f"\n{cleanup_error}"
        self.status.set_text(message)

    def unmount_drive(self, *_):
        if not os.path.ismount(self.mountpoint):
            self.status.set_text("The drive is not mounted.")
            self.mount_button.set_sensitive(True)
            self.unmount_button.set_sensitive(False)
            self.open_button.set_sensitive(False)
            return
        tool = shutil.which("fusermount3") or shutil.which("fusermount")
        if not tool:
            self.status.set_text("Install fusermount3 to safely unmount the drive.")
            return
        try:
            result = subprocess.run([tool, "-u", str(self.mountpoint)], capture_output=True, text=True)
            if result.returncode:
                self.status.set_text(f"Unmount failed: {result.stderr.strip()}")
                return
            if self.mount_process and self.mount_process.poll() is None:
                self.mount_process.terminate()
            self.mount_button.set_sensitive(True)
            self.unmount_button.set_sensitive(False)
            self.open_button.set_sensitive(False)
            cleanup_error = self._release_offset_device()
            self.status.set_text(cleanup_error or "Drive safely unmounted. You can disconnect it now.")
        except OSError as error:
            self.status.set_text(f"Unmount failed: {error}")

    def open_drive(self, *_):
        if os.path.ismount(self.mountpoint):
            try:
                opened = Gio.AppInfo.launch_default_for_uri(self.mountpoint.as_uri(), None)
                if opened:
                    self.status.set_text(f"Mounted at {self.mountpoint}. If no window appeared, click Open mounted drive again.")
                else:
                    self.status.set_text(f"Could not open the file manager. The drive is mounted at {self.mountpoint}.")
            except GLib.Error as error:
                self.status.set_text(f"Could not open the file manager: {error.message}. Drive mounted at {self.mountpoint}.")
        else:
            self.status.set_text("Mount the drive first.")

if __name__ == "__main__":
    DriveManager().run(None)
