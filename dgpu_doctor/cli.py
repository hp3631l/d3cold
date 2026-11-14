"""Command-line interface for d3cold."""

from __future__ import annotations

import argparse
import sys
import time

from .audit import run_audit
from .monitor import run_monitor
from .pci import find_nvidia_dgpu
from .remediate import (
    apply_live_power_control,
    are_system_rules_installed,
    fix_btop,
    fix_compositor_env,
    fix_system_rules,
    install_prime_run,
    suspend_dgpu,
)

G, Y, R, C, B, E = "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[1m", "\033[0m"


def cmd_audit(_args: argparse.Namespace) -> int:
    print(f"\n{B}{C}=== d3cold Diagnostics ==={E}\n")
    report = run_audit()
    sc = G if report.runtime_status == "suspended" else R
    print(f"{B}Target dGPU:{E}       {report.dgpu_name} ({report.dgpu_pci})")
    print(f"{B}Runtime Status:{E}    {sc}{report.runtime_status}{E}")
    print(f"{B}PCI Power Control:{E} {report.power_control}")
    bat = f"{report.battery_watts:.2f} W" if report.battery_watts else "AC Connected"
    print(f"{B}Battery Rate:{E}      {bat}\n" + "=" * 50 + "\n")

    for f in report.findings:
        icon = f"{G}✓ PASS{E}" if f.status == "PASS" else (f"{Y}⚠ WARN{E}" if f.status == "WARN" else f"{R}✗ FAIL{E}")
        print(f"[{icon}] {B}{f.category}:{E} {f.title}\n       Details: {f.details}")
        if f.remediation:
            print(f"       {C}Remedy:{E}  {f.remediation}")
        print()

    if report.has_failures:
        print(f"{R}{B}Diagnostics found issues preventing the dGPU from sleeping.{E}\nRun `d3cold fix` to remediate.\n")
        return 1
    print(f"{G}{B}All checks passed! Your dGPU is entering D3cold properly.{E}\n" if not report.has_warnings else f"{Y}Diagnostics passed with warnings.{E}\n")
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    print(f"\n{B}{C}=== Applying d3cold Remediations ==={E}\n")
    for name, fn in [("PCI Power Control", apply_live_power_control), ("Terminal Monitor", fix_btop), ("Game Offloader", install_prime_run)]:
        ok, msg = fn()
        icon = f"{G}✓{E}" if ok else f"{R}✗{E}"
        print(f"[{icon}] {name}: {msg}")

    for ok, msg in fix_compositor_env():
        icon = f"{G}✓{E}" if ok else f"{R}✗{E}"
        print(f"[{icon}] Compositor Config: {msg}")

    if are_system_rules_installed():
        print(f"[{G}✓{E}] System Rules: RTD3 udev & modprobe configurations are already active.")
    elif getattr(args, "system", False) or getattr(args, "all", False):
        for ok, msg in fix_system_rules():
            icon = f"{G}✓{E}" if ok else f"{R}✗{E}"
            print(f"[{icon}] System Rules: {msg}")
    else:
        print(f"\n{C}Note:{E} RTD3 rules not detected. Run `d3cold fix --system` to install.")

    print(f"\n{B}Initiating dGPU Sleep Transition...{E}")
    ok, msg, logs = suspend_dgpu(force=getattr(args, "force", False))
    for line in logs:
        print(f"  • {line}")
    icon = f"{G}✓{E}" if ok else f"{Y}⚠{E}"
    print(f"[{icon}] Suspension Result: {msg}\n\n{G}{B}Remediation complete.{E} Run `d3cold audit` or `d3cold watch` to verify.\n")
    return 0 if ok else 1


def cmd_suspend(args: argparse.Namespace) -> int:
    ok, msg, logs = suspend_dgpu(force=args.force)
    for l in logs:
        print(f"  • {l}")
    print(f"\n[{G if ok else R}✓{E}] {msg}\n")
    return 0 if ok else 1


def cmd_status(_args: argparse.Namespace) -> int:
    dgpu = find_nvidia_dgpu()
    if not dgpu:
        print("not_found")
        return 1
    print(dgpu.runtime_status)
    return 0 if dgpu.runtime_status == "suspended" else 2


def cmd_daemon(_args: argparse.Namespace) -> int:
    dgpu = find_nvidia_dgpu()
    if not dgpu:
        return 1
    ctrl = dgpu.sysfs_path / "power" / "control"
    p_ctrl = dgpu.sysfs_path.resolve().parent / "power" / "control"
    while True:
        try:
            if (ctrl.exists() and ctrl.read_text().strip() != "auto") or (p_ctrl.exists() and p_ctrl.read_text().strip() != "auto"):
                apply_live_power_control()
        except OSError:
            pass
        time.sleep(10)


def main() -> int:
    p = argparse.ArgumentParser(prog="d3cold", description="Zero-wake D3cold diagnostics and auto-remediation for NVIDIA hybrid laptops.")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("audit")
    sp = sub.add_parser("suspend")
    sp.add_argument("-f", "--force", action="store_true")
    fp = sub.add_parser("fix")
    fp.add_argument("--system", "--all", action="store_true", dest="system")
    fp.add_argument("-f", "--force", action="store_true")
    wp = sub.add_parser("watch", aliases=["monitor"])
    wp.add_argument("-i", "--interval", type=float, default=1.0)
    sub.add_parser("status")
    sub.add_parser("daemon")

    args = p.parse_args()
    cmd = args.command or "audit"
    handlers = {"audit": cmd_audit, "suspend": cmd_suspend, "fix": cmd_fix, "watch": lambda a: run_monitor(interval=a.interval), "monitor": lambda a: run_monitor(interval=a.interval), "status": cmd_status, "daemon": cmd_daemon}
    return handlers[cmd](args) if cmd in handlers else p.print_help() or 0


if __name__ == "__main__":
    sys.exit(main())
