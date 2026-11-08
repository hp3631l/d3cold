"""Safe PCI and sysfs discovery and query helpers.

CRITICAL RULE:
Functions in this module must NEVER perform operations that wake a sleeping dGPU.
- Do NOT run `nvidia-smi`
- Do NOT probe DRM connector `status` files (causes hardware DDC polling)
- Do NOT run `lspci` repeatedly (causes PCI config space access)
- Do NOT read `/proc/driver/nvidia/.../power` repeatedly (probes GPU registers)
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class GPUDevice:
    pci_address: str
    vendor_id: str
    device_id: str
    class_id: str
    is_nvidia: bool
    is_primary: bool
    sysfs_path: Path
    name: str = ""

    @property
    def runtime_status(self) -> str:
        status_file = self.sysfs_path / "power" / "runtime_status"
        if status_file.exists():
            try:
                return status_file.read_text().strip()
            except OSError:
                return "unknown"
        return "unsupported"

    @property
    def power_control(self) -> str:
        control_file = self.sysfs_path / "power" / "control"
        if control_file.exists():
            try:
                return control_file.read_text().strip()
            except OSError:
                return "unknown"
        return "unsupported"

    @property
    def power_state(self) -> str:
        state_file = self.sysfs_path / "power_state"
        if state_file.exists():
            try:
                return state_file.read_text().strip()
            except OSError:
                return "unknown"
        return "unknown"

    @property
    def is_suspended(self) -> bool:
        return self.runtime_status == "suspended" or self.power_state == "D3cold"


_CACHED_NAMES: dict[str, str] = {}


def _read_trimmed(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _lookup_device_name(addr: str) -> str:
    if addr in _CACHED_NAMES:
        return _CACHED_NAMES[addr]
    # Use udevadm info to read from cached udev database instead of lspci (which probes hardware)
    try:
        out = subprocess.check_output(
            ["udevadm", "info", "-q", "property", "-p", f"/sys/bus/pci/devices/{addr}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if line.startswith("ID_MODEL_FROM_DATABASE="):
                name = line.split("=", 1)[1].strip()
                _CACHED_NAMES[addr] = name
                return name
    except Exception:
        pass

    _CACHED_NAMES[addr] = "PCI Display Controller"
    return "PCI Display Controller"


def find_gpus() -> list[GPUDevice]:
    """Discover all display controllers on the PCI bus."""
    devices: list[GPUDevice] = []
    base = Path("/sys/bus/pci/devices")
    if not base.exists():
        return devices

    for dev in sorted(base.iterdir()):
        class_str = _read_trimmed(dev / "class")
        if not (class_str.startswith("0x0300") or class_str.startswith("0x0302") or class_str.startswith("0x0380")):
            continue

        vendor_id = _read_trimmed(dev / "vendor").lower()
        device_id = _read_trimmed(dev / "device").lower()
        boot_vga = _read_trimmed(dev / "boot_vga") == "1"

        is_nvidia = vendor_id == "0x10de"
        name = _lookup_device_name(dev.name)

        devices.append(
            GPUDevice(
                pci_address=dev.name,
                vendor_id=vendor_id,
                device_id=device_id,
                class_id=class_str,
                is_nvidia=is_nvidia,
                is_primary=boot_vga,
                sysfs_path=dev,
                name=name,
            )
        )
    return devices


def find_nvidia_dgpu() -> Optional[GPUDevice]:
    """Find the discrete NVIDIA GPU."""
    for gpu in find_gpus():
        if gpu.is_nvidia:
            return gpu
    return None


def find_nvidia_audio() -> Optional[Path]:
    """Find the NVIDIA HDMI/DP Audio controller function (0x040300) on the same slot."""
    base = Path("/sys/bus/pci/devices")
    if not base.exists():
        return None
    for dev in base.iterdir():
        class_str = _read_trimmed(dev / "class")
        vendor_id = _read_trimmed(dev / "vendor").lower()
        if vendor_id == "0x10de" and class_str.startswith("0x0403"):
            return dev
    return None


def get_nvidia_client_processes() -> list[dict]:
    """Find processes holding open file descriptors to /dev/nvidia* or /dev/dri render nodes.

    Scans /proc directly without calling nvidia-smi or opening devices.
    """
    clients = []
    for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            target = os.readlink(fd_path)
            if target.startswith("/dev/nvidia") or target.startswith("/dev/dri/renderD128") or target.startswith("/dev/dri/card1"):
                pid = fd_path.split("/")[2]
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as cf:
                        cmd = cf.read().decode("utf-8", errors="replace").replace("\x00", " ").strip()
                    with open(f"/proc/{pid}/comm", "r") as cmf:
                        comm = cmf.read().strip()
                except OSError:
                    cmd, comm = "unknown", "unknown"
                clients.append({
                    "pid": int(pid),
                    "comm": comm,
                    "cmdline": cmd,
                    "target": target,
                })
        except OSError:
            pass

    # Deduplicate by PID
    seen = set()
    deduped = []
    for c in clients:
        if c["pid"] not in seen:
            seen.add(c["pid"])
            deduped.append(c)
    return deduped


def get_battery_energy_rate() -> Optional[float]:
    """Read the current system battery discharge rate in Watts."""
    for bat in glob.glob("/sys/class/power_supply/BAT*"):
        bat_path = Path(bat)
        power_file = bat_path / "power_now"
        current_file = bat_path / "current_now"
        voltage_file = bat_path / "voltage_now"
        try:
            if power_file.exists():
                return int(power_file.read_text().strip()) / 1e6
            if current_file.exists() and voltage_file.exists():
                c = int(current_file.read_text().strip())
                v = int(voltage_file.read_text().strip())
                return (c * v) / 1e12
        except (ValueError, OSError):
            pass
    return None


def get_cpu_package_power(duration: float = 0.5) -> Optional[float]:
    """Calculate CPU Package power draw via Intel RAPL over a short sampling interval."""
    import time
    rapl_path = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj")
    if not rapl_path.exists():
        return None
    try:
        e1 = int(rapl_path.read_text().strip())
        t1 = time.time()
        time.sleep(duration)
        e2 = int(rapl_path.read_text().strip())
        t2 = time.time()
        return (e2 - e1) / (t2 - t1) / 1e6
    except (ValueError, OSError):
        return None
