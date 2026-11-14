"""Diagnostics engine for NVIDIA hybrid laptop power drain."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .pci import (
    _read_trimmed,
    find_gpus,
    find_nvidia_audio,
    find_nvidia_dgpu,
    get_battery_energy_rate,
    get_cpu_package_power,
    get_nvidia_client_processes,
)


@dataclass
class AuditFinding:
    category: str
    status: str  # "PASS", "WARN", "FAIL"
    title: str
    details: str
    remediation: str = ""


@dataclass
class AuditReport:
    findings: List[AuditFinding] = field(default_factory=list)
    dgpu_name: str = "Unknown NVIDIA GPU"
    dgpu_pci: str = "None"
    runtime_status: str = "unknown"
    power_control: str = "unknown"
    battery_watts: float | None = None
    battery_status: str = "Unknown"
    cpu_package_watts: float | None = None

    def add(self, cat: str, status: str, title: str, details: str, remedy: str = "") -> None:
        self.findings.append(AuditFinding(cat, status, title, details, remedy))

    @property
    def has_failures(self) -> bool:
        return any(f.status == "FAIL" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.status == "WARN" for f in self.findings)


def run_audit() -> AuditReport:
    """Run all 5 audit layers and return a structured report."""
    report = AuditReport()
    report.battery_watts, report.battery_status = get_battery_energy_rate()

    dgpu = find_nvidia_dgpu()
    if not dgpu:
        report.add("Hardware", "WARN", "No NVIDIA discrete GPU found on PCI bus", "No PCI device with vendor ID 0x10de and display class detected.")
        return report

    report.dgpu_name, report.dgpu_pci = dgpu.name, dgpu.pci_address
    report.runtime_status, report.power_control = dgpu.runtime_status, dgpu.power_control

    # Layer 1: PCI Runtime Power Management
    if dgpu.runtime_status == "suspended":
        report.add("Runtime PM", "PASS", "dGPU is in deep sleep (D3cold)", f"Device {dgpu.pci_address} is powered off and suspended by kernel PCI PM.")
    else:
        report.add("Runtime PM", "FAIL", f"dGPU is stuck awake ({dgpu.runtime_status})", f"Device {dgpu.pci_address} is powered up in state '{dgpu.runtime_status}'.", "Run `d3cold fix` to apply RTD3 rules and compositor environment.")

    if dgpu.power_control == "auto":
        report.add("Runtime PM", "PASS", "PCI power/control is set to 'auto'", f"{dgpu.sysfs_path}/power/control allows automatic runtime suspension.")
    else:
        report.add("Runtime PM", "FAIL", f"PCI power/control is '{dgpu.power_control}' (blocks sleep)", f"{dgpu.sysfs_path}/power/control must be 'auto'.", "Run `d3cold fix` to set runtime power management to auto.")

    # Layer 2: NVIDIA Audio Controller
    audio_dev = find_nvidia_audio()
    if audio_dev and audio_dev.exists():
        driver = audio_dev / "driver"
        if driver.exists() and driver.resolve().name == "snd_hda_intel":
            report.add("Audio Controller", "FAIL", f"NVIDIA Audio ({audio_dev.name}) is bound to snd_hda_intel", "WirePlumber/PipeWire audio probing keeps the GPU awake.", "Run `d3cold fix` to deploy udev rules that remove audio function.")
        else:
            report.add("Audio Controller", "PASS", "NVIDIA Audio controller is unbound / removed", "No audio controller on the NVIDIA slot is preventing D3cold.")
    else:
        report.add("Audio Controller", "PASS", "NVIDIA Audio controller is unbound / removed", "No audio controller on the NVIDIA slot is preventing D3cold.")

    # Layer 3: Persistence Mode / Daemon
    params_file = Path("/proc/driver/nvidia/params")
    p_mode = bool(re.search(r"PersistenceMode:\s*1", params_file.read_text())) if params_file.exists() else False
    if p_mode:
        report.add("Persistence Mode", "FAIL", "NVIDIA driver persistence mode is ON", "GPU driver maintains persistent state in VRAM.", "Run `nvidia-smi -pm 0` or `d3cold fix` to disable persistence mode.")
    else:
        report.add("Persistence Mode", "PASS", "nvidia-persistenced is inactive", "Persistence daemon is not keeping driver initialized.")

    # Layer 3b: Udev Rules
    has_udev = any(Path(p).exists() and "0x10de" in Path(p).read_text() for p in ("/etc/udev/rules.d/80-nvidia-pm.rules", "/etc/udev/rules.d/99-nvidia-pm.rules", "/lib/udev/rules.d/80-nvidia-pm.rules"))
    if has_udev:
        report.add("Udev Rules", "PASS", "NVIDIA Runtime PM udev rule is present", "Udev rule is configured to manage power/control on bind/unbind.")
    else:
        report.add("Udev Rules", "WARN", "NVIDIA Runtime PM udev rule missing", "No udev rule found to automatically set power/control to auto.", "Run `d3cold fix --system` to install /etc/udev/rules.d/80-nvidia-pm.rules.")

    # Layer 3c: Modprobe Configuration
    has_modprobe = any(Path(p).exists() and "NVreg_DynamicPowerManagement=0x02" in Path(p).read_text() for p in ("/etc/modprobe.d/nvidia-pm.conf", "/etc/modprobe.d/nvidia.conf"))
    if has_modprobe:
        report.add("Modprobe", "PASS", "NVIDIA DynamicPowerManagement is configured", "Kernel module options specify RTD3 dynamic power management.")
    else:
        report.add("Modprobe", "WARN", "NVreg_DynamicPowerManagement=0x02 missing", "Kernel module is not explicitly configured for dynamic power management.", "Run `d3cold fix --system` to configure /etc/modprobe.d/nvidia-pm.conf.")

    # Layer 4: Compositor Primary GPU & Gaming Environment Isolation
    from .pci import find_igpu_drm_card
    igpu_card = find_igpu_drm_card()
    aq_drm = os.environ.get("AQ_DRM_DEVICES", "")
    h_conf, u_conf, u_base = Path.home() / ".config/environment.d/10-hyprland-gpu.conf", Path.home() / ".config/uwsm/env-hyprland", Path.home() / ".config/uwsm/env"

    has_aq = bool(igpu_card and (igpu_card in aq_drm or (h_conf.exists() and igpu_card in h_conf.read_text()) or (u_conf.exists() and igpu_card in u_conf.read_text())))
    if has_aq:
        report.add("Compositor GPU", "PASS", "Compositor display engine pinned to iGPU", f"AQ_DRM_DEVICES pins primary render node to {igpu_card}.")
    else:
        report.add("Compositor GPU", "WARN", "Compositor display engine not pinned to iGPU", "AQ_DRM_DEVICES not explicitly configuring iGPU as primary.", "Run `d3cold fix` to pin compositor display engine to iGPU.")

    has_vk = bool(os.environ.get("VK_DRIVER_FILES")) or any(f.exists() and "VK_DRIVER_FILES" in f.read_text() for f in (h_conf, u_conf, u_base))
    has_egl = bool(os.environ.get("__EGL_VENDOR_LIBRARY_FILENAMES")) or any(f.exists() and "__EGL_VENDOR_LIBRARY_FILENAMES" in f.read_text() for f in (h_conf, u_conf, u_base))
    if has_vk or has_egl:
        report.add("Gaming Environment", "FAIL", "Session environment contains global driver locks", "Global VK_DRIVER_FILES or __EGL_VENDOR_LIBRARY_FILENAMES starves games from discovering NVIDIA Vulkan/EGL.", "Run `d3cold fix` to purge global driver locks.")
    else:
        report.add("Gaming Environment", "PASS", "Gaming and Vulkan ICD discovery is clean", "No global session locks detected. Games can offload to NVIDIA seamlessly.")

    # Layer 5: Terminal Monitors (btop)
    btop_conf = Path.home() / ".config/btop/btop.conf"
    if btop_conf.exists():
        btxt = btop_conf.read_text()
        has_nvml = bool(re.search(r"^\s*nvml_measure_pcie_speeds\s*=\s*true", btxt, re.MULTILINE))
        has_nv_shown = bool(re.search(r'^\s*shown_gpus\s*=\s*"[^"]*nvidia', btxt, re.MULTILINE | re.IGNORECASE))
        if has_nvml or has_nv_shown:
            report.add("Terminal Monitors", "WARN", "btop is polling NVIDIA GPU via NVML", "btop.conf polls PCIe bandwidth or NVML on the dGPU.", "Run `d3cold fix` to isolate btop monitoring to iGPU.")
        else:
            report.add("Terminal Monitors", "PASS", "btop configuration is optimized for iGPU only", "NVML polling and PCIe bandwidth measurements on NVIDIA are disabled.")

    # Active Clients
    clients = get_nvidia_client_processes()
    if clients:
        procs = ", ".join(f"{c['comm']} (PID {c['pid']})" for c in clients[:5])
        report.add("Active Clients", "FAIL", f"{len(clients)} process(es) holding /dev/nvidia*", f"Active clients: {procs}", "Close GPU-accelerated applications or run `d3cold fix -f`.")
    else:
        report.add("Active Clients", "PASS", "No active processes holding /dev/nvidia*", "Zero open file descriptors to NVIDIA driver files found in /proc.")

    # Parent PCIe Root Port Bridge
    try:
        parent = dgpu.sysfs_path.resolve().parent
        p_ctrl = _read_trimmed(parent / "power" / "control")
        if p_ctrl == "auto":
            report.add("PCIe Root Port", "PASS", f"Parent PCIe bridge {parent.name} is set to 'auto'", f"{parent}/power/control allows upstream link power gating.")
        else:
            report.add("PCIe Root Port", "FAIL", f"Parent PCIe bridge {parent.name} power/control is '{p_ctrl}'", f"Parent bridge must be 'auto' for L2/L3 link sleep.", "Run `d3cold fix` to set parent bridge power/control to auto.")
    except Exception:
        pass

    return report
