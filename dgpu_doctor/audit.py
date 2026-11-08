"""Diagnostics engine for NVIDIA hybrid laptop power drain.

Performs a 5-layer audit to pinpoint why a discrete GPU refuses to enter D3cold.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .pci import (
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
    cpu_package_watts: float | None = None

    @property
    def has_failures(self) -> bool:
        return any(f.status == "FAIL" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.status == "WARN" for f in self.findings)


def run_audit() -> AuditReport:
    """Run all 5 audit layers and return a structured report."""
    report = AuditReport()
    report.battery_watts = get_battery_energy_rate()

    dgpu = find_nvidia_dgpu()
    if not dgpu:
        report.findings.append(
            AuditFinding(
                category="Hardware",
                status="WARN",
                title="No NVIDIA discrete GPU found on PCI bus",
                details="No PCI device with vendor ID 0x10de and display class was detected.",
            )
        )
        return report

    report.dgpu_name = dgpu.name
    report.dgpu_pci = dgpu.pci_address
    report.runtime_status = dgpu.runtime_status
    report.power_control = dgpu.power_control

    # Layer 1: PCI Runtime Power Management State
    if dgpu.runtime_status == "suspended":
        report.findings.append(
            AuditFinding(
                category="Runtime PM",
                status="PASS",
                title="dGPU is in deep sleep (D3cold)",
                details=f"Device {dgpu.pci_address} is powered off and suspended by kernel PCI PM.",
            )
        )
    else:
        report.findings.append(
            AuditFinding(
                category="Runtime PM",
                status="FAIL",
                title=f"dGPU is stuck awake ({dgpu.runtime_status})",
                details=f"Device {dgpu.pci_address} is powered up in state '{dgpu.runtime_status}'.",
                remediation="Run `dgpu-doctor fix` to apply RTD3 udev rules, compositor environment, and terminate wake loops.",
            )
        )

    if dgpu.power_control == "auto":
        report.findings.append(
            AuditFinding(
                category="Runtime PM",
                status="PASS",
                title="PCI power/control is set to 'auto'",
                details=f"{dgpu.sysfs_path}/power/control allows automatic runtime suspension.",
            )
        )
    else:
        report.findings.append(
            AuditFinding(
                category="Runtime PM",
                status="FAIL",
                title=f"PCI power/control is '{dgpu.power_control}' (inhibiting sleep)",
                details="The kernel is forbidden from suspending the PCI slot because control is not 'auto'.",
                remediation="Set power/control to 'auto' via udev rule or `dgpu-doctor fix`.",
            )
        )

    # Layer 2: NVIDIA Multifunction Audio Device (0000:xx:00.1)
    audio_dev = find_nvidia_audio()
    if audio_dev and audio_dev.exists():
        # Check if driver is bound
        driver_link = audio_dev / "driver"
        if driver_link.exists():
            report.findings.append(
                AuditFinding(
                    category="Audio Controller",
                    status="FAIL",
                    title="NVIDIA Audio controller is active and holding dGPU awake",
                    details=f"Audio function {audio_dev.name} is bound to {driver_link.resolve().name}. WirePlumber/PipeWire may hold /dev/snd/controlC* open.",
                    remediation="Add an unbind udev rule or run `dgpu-doctor fix` to remove the audio device on boot.",
                )
            )
        else:
            report.findings.append(
                AuditFinding(
                    category="Audio Controller",
                    status="WARN",
                    title="NVIDIA Audio controller exists but has no driver bound",
                    details=f"Device {audio_dev.name} is present on the bus.",
                )
            )
    else:
        report.findings.append(
            AuditFinding(
                category="Audio Controller",
                status="PASS",
                title="NVIDIA Audio controller is unbound / removed",
                details="No audio controller on the NVIDIA slot is preventing D3cold transition.",
            )
        )

    # Layer 3: Kernel Module Parameters and Udev Rules
    udev_pm_file = Path("/etc/udev/rules.d/80-nvidia-pm.rules")
    if udev_pm_file.exists() and "0x030000" in udev_pm_file.read_text():
        report.findings.append(
            AuditFinding(
                category="Udev Rules",
                status="PASS",
                title="NVIDIA Runtime PM udev rule is present",
                details=f"{udev_pm_file} is configured to manage power/control on bind/unbind.",
            )
        )
    else:
        report.findings.append(
            AuditFinding(
                category="Udev Rules",
                status="FAIL",
                title="Missing /etc/udev/rules.d/80-nvidia-pm.rules",
                details="Kernel udev rules are not set to automate NVIDIA runtime PM upon driver bind.",
                remediation="Run `sudo dgpu-doctor fix` to deploy the ArchWiki recommended 80-nvidia-pm.rules.",
            )
        )

    # Check modprobe parameter
    modprobe_files = list(Path("/etc/modprobe.d").glob("*.conf")) if Path("/etc/modprobe.d").exists() else []
    has_dpm = False
    for mf in modprobe_files:
        try:
            content = mf.read_text()
            if "NVreg_DynamicPowerManagement=0x0" in content:
                has_dpm = True
                break
        except OSError:
            pass

    if has_dpm:
        report.findings.append(
            AuditFinding(
                category="Modprobe",
                status="PASS",
                title="NVIDIA DynamicPowerManagement is configured",
                details="Kernel module options in /etc/modprobe.d specify RTD3 dynamic power management.",
            )
        )
    else:
        report.findings.append(
            AuditFinding(
                category="Modprobe",
                status="WARN",
                title="NVreg_DynamicPowerManagement not explicitly set in /etc/modprobe.d",
                details="The driver may rely on defaults which are unreliable on certain OEM BIOS implementations.",
                remediation="Set `options nvidia NVreg_DynamicPowerManagement=0x02` in /etc/modprobe.d/nvidia.conf.",
            )
        )

    # Layer 4: Compositor EGL Library Leak
    egl_env = os.environ.get("__EGL_VENDOR_LIBRARY_FILENAMES", "")
    hypr_env_file = Path.home() / ".config/environment.d/10-hyprland-gpu.conf"
    uwsm_env_file = Path.home() / ".config/uwsm/env-hyprland"
    mesa_json = "/usr/share/glvnd/egl_vendor.d/50_mesa.json"

    is_egl_pinned = "50_mesa.json" in egl_env
    if not is_egl_pinned and hypr_env_file.exists():
        is_egl_pinned = "50_mesa.json" in hypr_env_file.read_text()
    if not is_egl_pinned and uwsm_env_file.exists():
        is_egl_pinned = "50_mesa.json" in uwsm_env_file.read_text()

    if is_egl_pinned:
        report.findings.append(
            AuditFinding(
                category="Compositor EGL",
                status="PASS",
                title="Compositor EGL vendor pinned to Mesa",
                details="__EGL_VENDOR_LIBRARY_FILENAMES is restricted to 50_mesa.json, preventing 10_nvidia.json file descriptor leaks.",
            )
        )
    else:
        report.findings.append(
            AuditFinding(
                category="Compositor EGL",
                status="WARN",
                title="__EGL_VENDOR_LIBRARY_FILENAMES is unpinned",
                details="libglvnd will probe 10_nvidia.json on compositor start, leaving open file descriptors to /dev/nvidia0.",
                remediation="Run `dgpu-doctor fix` to isolate compositor EGL to Mesa.",
            )
        )

    # Layer 5: Terminal Monitors (btop)
    btop_conf = Path.home() / ".config/btop/btop.conf"
    if btop_conf.exists():
        btop_text = btop_conf.read_text()
        has_nvml_measure = bool(
            re.search(r'^\s*nvml_measure_pcie_speeds\s*=\s*true', btop_text, re.MULTILINE)
        )
        has_nvidia_shown = bool(
            re.search(r'^\s*shown_gpus\s*=\s*"[^"]*nvidia[^"]*"', btop_text, re.MULTILINE)
        )

        if has_nvml_measure or has_nvidia_shown:
            report.findings.append(
                AuditFinding(
                    category="Terminal Monitors",
                    status="FAIL",
                    title="btop is configured to poll NVIDIA via NVML",
                    details=f"In {btop_conf}, shown_gpus or nvml_measure_pcie_speeds queries the RTX card every 500ms, preventing sleep.",
                    remediation="Run `dgpu-doctor fix` to set shown_gpus = 'intel' and nvml_measure_pcie_speeds = false.",
                )
            )
        else:
            report.findings.append(
                AuditFinding(
                    category="Terminal Monitors",
                    status="PASS",
                    title="btop configuration is optimized for iGPU only",
                    details="NVML polling and PCIe bandwidth measurements on NVIDIA are disabled.",
                )
            )

    # Active Client Processes
    clients = get_nvidia_client_processes()
    if clients:
        proc_list = ", ".join(f"{c['comm']} (PID {c['pid']})" for c in clients[:3])
        report.findings.append(
            AuditFinding(
                category="Active Clients",
                status="WARN",
                title=f"{len(clients)} process(es) currently using /dev/nvidia*",
                details=f"Active clients: {proc_list}. GPU cannot sleep while clients are active.",
                remediation="Close the listed applications or verify if they are background compute jobs.",
            )
        )
    else:
        report.findings.append(
            AuditFinding(
                category="Active Clients",
                status="PASS",
                title="No active processes holding /dev/nvidia*",
                details="Zero open file descriptors to NVIDIA driver files found in /proc.",
            )
        )

    return report
