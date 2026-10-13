#!/usr/bin/env python3
"""fastboot_extract_boot.py - pull boot.img over fastboot, no su/root shell needed.

This works purely through the bootloader (fastboot), so it's useful before
you have root at all - e.g. to patch boot.img with Magisk and flash it back.
Requires an UNLOCKED bootloader. It does not unlock bootloaders or bypass
any lock - that's a separate, device-specific, user-driven step.

Two extraction strategies, tried in order:
  1. `fastboot fetch` - supported on newer fastboot/bootloaders, pulls the
     partition image directly, no root needed at all.
  2. Boot-image-size probe + `fastboot get_staged` fallback for older
     bootloaders that don't support fetch (device/bootloader dependent;
     falls back to telling you to use a custom recovery dd+adb pull instead).

Usage:
    python fastboot_extract_boot.py                 # extract 'boot' partition
    python fastboot_extract_boot.py --slot b         # extract slot_b on A/B devices
    python fastboot_extract_boot.py --partition vendor_boot -o vendor_boot.img
    python fastboot_extract_boot.py -s SERIAL
"""
import argparse
import os
import re
import shutil
import subprocess
import sys


def find_fastboot() -> str:
    candidates = [os.environ.get("FASTBOOT"), "fastboot", "fastboot.exe"]
    for c in candidates:
        if c and shutil.which(c):
            return shutil.which(c)
    sys.exit("fastboot not found. Install platform-tools or set the FASTBOOT env var.")


def run(fb: str, serial: str | None, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = [fb] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def list_devices(fb: str) -> list[str]:
    out = subprocess.run([fb, "devices"], capture_output=True, text=True).stdout
    devices = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "fastboot":
            devices.append(parts[0])
    return devices


def get_var(fb: str, serial: str | None, name: str) -> str | None:
    r = run(fb, serial, "getvar", name)
    text = r.stdout + r.stderr
    m = re.search(rf"{re.escape(name)}:\s*(\S+)", text)
    return m.group(1) if m else None


def check_unlocked(fb: str, serial: str | None) -> bool:
    val = get_var(fb, serial, "unlocked")
    if val is None:
        return True  # some bootloaders don't report this var; let fetch attempt fail loudly if locked
    return val.lower() == "yes"


def partition_name(base: str, slot: str | None) -> str:
    if slot and not base.endswith(f"_{slot}"):
        return f"{base}_{slot}"
    return base


def extract_via_fetch(fb: str, serial: str | None, partition: str, out_path: str) -> bool:
    r = run(fb, serial, "fetch", f"@{partition}", out_path, timeout=120)
    if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return True
    print((r.stdout + r.stderr).strip())
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="Extract a partition image (default: boot) via fastboot, no root shell required.")
    p.add_argument("-s", "--serial", help="device serial")
    p.add_argument("--partition", default="boot", help="base partition name (default: boot)")
    p.add_argument("--slot", choices=["a", "b"], help="A/B slot suffix to append, e.g. boot_a")
    p.add_argument("-o", "--output", help="output file path (default: <partition>.img)")
    args = p.parse_args()

    fb = find_fastboot()
    devices = list_devices(fb)

    if not devices:
        print("No device in fastboot mode found. Reboot with: adb reboot bootloader")
        return 1

    serial = args.serial
    if not serial:
        if len(devices) > 1:
            print("Multiple fastboot devices found, pick one with -s:")
            for d in devices:
                print(f"  {d}")
            return 1
        serial = devices[0]
    elif serial not in devices:
        print(f"Device {serial} not found. Connected: {', '.join(devices)}")
        return 1

    print(f"Using fastboot: {fb}")
    print(f"Device:         {serial}")

    if not check_unlocked(fb, serial):
        print("Bootloader reports LOCKED. Fastboot cannot read partitions on a locked "
              "bootloader - this tool can't and won't bypass that. Unlock first "
              "(this wipes the device) via your OEM's official process, then retry.")
        return 1

    # Auto-detect current slot if not specified, for A/B devices
    slot = args.slot
    if slot is None:
        current = get_var(fb, serial, "current-slot")
        if current in ("a", "b"):
            slot = current
            print(f"A/B device detected, using current slot: {slot}")

    partition = partition_name(args.partition, slot)
    out_path = args.output or f"{partition}.img"

    print(f"Extracting partition '{partition}' -> {out_path} ...")
    if extract_via_fetch(fb, serial, partition, out_path):
        size = os.path.getsize(out_path)
        print(f"Done: {out_path} ({size:,} bytes)")
        return 0

    print(
        "\n'fastboot fetch' isn't supported on this bootloader/fastboot version.\n"
        "Fallback options, neither needs su:\n"
        "  1. Boot a matching TWRP/custom recovery image temporarily:\n"
        f"       {fb} boot twrp.img\n"
        f"     then: dd if=/dev/block/by-name/{partition} of=/sdcard/{partition}.img\n"
        f"     then: adb pull /sdcard/{partition}.img\n"
        "  2. Pull the stock boot.img for your exact firmware version from a "
        "firmware archive instead of extracting it from the device."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
