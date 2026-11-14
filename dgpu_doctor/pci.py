"""Safe PCI and sysfs discovery helpers (zero-wake design)."""

from __future__ import annotations

import glob
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


def _read_trimmed(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


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
        return _read_trimmed(self.sysfs_path / "power" / "runtime_status") or "unsupported"

    @property
    def power_control(self) -> str:
        return _read_trimmed(self.sysfs_path / "power" / "control") or "unsupported"

    @property
    def power_state(self) -> str:
        return _read_trimmed(self.sysfs_path / "power_state") or "unknown"

    @property
    def is_suspended(self) -> bool:
        return self.runtime_status == "suspended" or self.power_state == "D3cold"


_CACHED_NAMES: dict[str, str] = {}


def _lookup_device_name(addr: str) -> str:
    if addr not in _CACHED_NAMES:
        try:
            out = subprocess.check_output(
                ["udevadm", "info", "-q", "property", "-p", f"/sys/bus/pci/devices/{addr}"],
                text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if line.startswith("ID_MODEL_FROM_DATABASE="):
                    _CACHED_NAMES[addr] = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
        _CACHED_NAMES.setdefault(addr, "PCI Display Controller")
    return _CACHED_NAMES[addr]


def find_gpus() -> list[GPUDevice]:
    devices = []
    base = Path("/sys/bus/pci/devices")
    if not base.exists():
        return devices
    for dev in sorted(base.iterdir()):
        cls = _read_trimmed(dev / "class")
        if cls.startswith(("0x0300", "0x0302", "0x0380")):
            vid = _read_trimmed(dev / "vendor").lower()
            devices.append(GPUDevice(
                pci_address=dev.name,
                vendor_id=vid,
                device_id=_read_trimmed(dev / "device").lower(),
                class_id=cls,
                is_nvidia=(vid == "0x10de"),
                is_primary=(_read_trimmed(dev / "boot_vga") == "1"),
                sysfs_path=dev,
                name=_lookup_device_name(dev.name),
            ))
    return devices


def find_nvidia_dgpu() -> Optional[GPUDevice]:
    return next((g for g in find_gpus() if g.is_nvidia), None)


def find_igpu() -> Optional[GPUDevice]:
    return next((g for g in find_gpus() if not g.is_nvidia), None)


def _find_drm_card(gpu: Optional[GPUDevice], fallback: Optional[str] = None) -> Optional[str]:
    if gpu:
        by_path = Path(f"/dev/dri/by-path/pci-{gpu.pci_address}-card")
        if by_path.exists():
            return str(by_path.resolve())
        drm_dir = gpu.sysfs_path / "drm"
        if drm_dir.exists():
            cards = sorted(drm_dir.glob("card*"))
            if cards:
                return f"/dev/dri/{cards[0].name}"
    for c in ("/dev/dri/card2", "/dev/dri/card1", "/dev/dri/card0"):
        if fallback and Path(c).exists():
            return c
    return fallback


def find_igpu_drm_card() -> str:
    return _find_drm_card(find_igpu(), fallback="/dev/dri/card2") or "/dev/dri/card2"


def find_dgpu_drm_card() -> Optional[str]:
    return _find_drm_card(find_nvidia_dgpu())


def find_nvidia_audio() -> Optional[Path]:
    base = Path("/sys/bus/pci/devices")
    if base.exists():
        for dev in base.iterdir():
            if _read_trimmed(dev / "vendor").lower() == "0x10de" and _read_trimmed(dev / "class").startswith("0x0403"):
                return dev
    return None


def get_nvidia_client_processes() -> list[dict]:
    clients = []
    compositors = {"hyprland", "xwayland", "sway", "kwin", "gnome-shell", "mutter", "wayfire"}
    for fd_path in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            target = os.readlink(fd_path)
            is_nv = target.startswith("/dev/nvidia")
            is_render = target.startswith("/dev/dri/renderD128")
            if is_nv or is_render:
                pid = fd_path.split("/")[2]
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as cf:
                        cmd = cf.read().decode("utf-8", errors="replace").replace("\x00", " ").strip()
                    with open(f"/proc/{pid}/comm", "r") as cmf:
                        comm = cmf.read().strip()
                except OSError:
                    cmd, comm = "unknown", "unknown"
                if is_render and comm.lower() in compositors:
                    continue
                clients.append({"pid": int(pid), "comm": comm, "cmdline": cmd, "target": target})
        except OSError:
            pass
    # Deduplicate by PID
    seen = set()
    return [c for c in clients if not (c["pid"] in seen or seen.add(c["pid"]))]


def get_battery_energy_rate() -> Tuple[Optional[float], str]:
    for bat in map(Path, glob.glob("/sys/class/power_supply/BAT*")):
        status = _read_trimmed(bat / "status") or "Unknown"
        try:
            p_file = bat / "power_now"
            if p_file.exists():
                return int(p_file.read_text().strip()) / 1e6, status
            c_file, v_file = bat / "current_now", bat / "voltage_now"
            if c_file.exists() and v_file.exists():
                return (int(c_file.read_text().strip()) * int(v_file.read_text().strip())) / 1e12, status
        except (ValueError, OSError):
            pass
    return None, "AC"


def get_cpu_package_power(duration: float = 0.5) -> Optional[float]:
    rapl = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj")
    if not rapl.exists():
        return None
    try:
        e1, t1 = int(rapl.read_text().strip()), time.time()
        time.sleep(duration)
        e2, t2 = int(rapl.read_text().strip()), time.time()
        return (e2 - e1) / (t2 - t1) / 1e6
    except (ValueError, OSError):
        return None
