"""Command-line interface for dgpu-doctor."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .audit import run_audit
from .monitor import run_monitor
from .pci import find_nvidia_dgpu, get_battery_energy_rate
from .remediate import (
    apply_live_power_control,
    are_system_rules_installed,
    fix_btop,
    fix_compositor_env,
    fix_system_rules,
    install_prime_run,
)

BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"


def cmd_audit(_args: argparse.Namespace) -> int:
    """Run full diagnostic audit and print formatted report."""
    print(f"\n{BOLD}{CYAN}=== d3cold Diagnostics ==={RESET}\n")

    report = run_audit()
    print(f"{BOLD}Target dGPU:{RESET}       {report.dgpu_name} ({report.dgpu_pci})")
    status_color = GREEN if report.runtime_status == "suspended" else RED
    print(f"{BOLD}Runtime Status:{RESET}    {status_color}{report.runtime_status}{RESET}")
    print(f"{BOLD}PCI Power Control:{RESET} {report.power_control}")
    if report.battery_watts is not None:
        if report.battery_status.lower() == "charging":
            print(f"{BOLD}Battery Charge Rate:{RESET} +{report.battery_watts:.2f} W (AC Connected)")
        elif report.battery_status.lower() == "full":
            print(f"{BOLD}Power Status:{RESET}       Full (AC Connected)")
        else:
            print(f"{BOLD}Battery Discharge:{RESET} {report.battery_watts:.2f} W")
    else:
        print(f"{BOLD}Power Status:{RESET}       AC Connected")
    print("\n" + "=" * 50 + "\n")

    for f in report.findings:
        if f.status == "PASS":
            icon = f"{GREEN}✓ PASS{RESET}"
        elif f.status == "WARN":
            icon = f"{YELLOW}⚠ WARN{RESET}"
        else:
            icon = f"{RED}✗ FAIL{RESET}"

        print(f"[{icon}] {BOLD}{f.category}:{RESET} {f.title}")
        print(f"       Details: {f.details}")
        if f.remediation:
            print(f"       {CYAN}Remedy:{RESET}  {f.remediation}")
        print()

    if report.has_failures:
        print(f"{RED}{BOLD}Diagnostics found issues preventing the dGPU from sleeping.{RESET}")
        print("Run `d3cold fix` to automatically remediate them.\n")
        return 1
    elif report.has_warnings:
        print(f"{YELLOW}Diagnostics passed with warnings.{RESET}\n")
        return 0
    else:
        print(f"{GREEN}{BOLD}All checks passed! Your dGPU is entering D3cold properly.{RESET}\n")
        return 0


def cmd_fix(args: argparse.Namespace) -> int:
    """Apply auto-remediations."""
    print(f"\n{BOLD}{CYAN}=== Applying d3cold Remediations ==={RESET}\n")

    # 1. Live power control
    success, msg = apply_live_power_control()
    badge = f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"
    print(f"[{badge}] PCI Power Control: {msg}")

    # 2. btop optimization
    success, msg = fix_btop()
    badge = f"{GREEN}✓{RESET}" if success else f"{YELLOW}⚠{RESET}"
    print(f"[{badge}] Terminal Monitor: {msg}")

    # 3. Compositor environment
    for success, msg in fix_compositor_env():
        badge = f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"
        print(f"[{badge}] Compositor Config: {msg}")

    # 4. prime-run wrapper
    success, msg = install_prime_run()
    badge = f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"
    print(f"[{badge}] Game Offloader: {msg}")

    # 5. System rules
    if are_system_rules_installed():
        print(f"[{GREEN}✓{RESET}] System Rules: RTD3 udev & modprobe configurations are already active.")
    elif args.system or args.all:
        print(f"\nDeploying system-wide udev and modprobe rules (sudo required)...")
        for success, msg in fix_system_rules():
            badge = f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"
            print(f"[{badge}] System Rules: {msg}")
    else:
        print(f"\n{CYAN}Note:{RESET} RTD3 rules not detected. Run with `--system` or `--all` to deploy.")

    # 6. Active Suspension
    force = getattr(args, "force", False)
    print(f"\n{BOLD}Initiating dGPU Sleep Transition...{RESET}")
    from .remediate import suspend_dgpu
    ok, msg, logs = suspend_dgpu(force=force)
    for line in logs:
        print(f"  • {line}")
    badge = f"{GREEN}✓{RESET}" if ok else f"{YELLOW}⚠{RESET}"
    print(f"[{badge}] Suspension Result: {msg}")

    print(f"\n{GREEN}{BOLD}Remediation complete.{RESET} Run `d3cold audit` or `d3cold watch` to verify.\n")
    return 0 if ok else 1


def cmd_suspend(args: argparse.Namespace) -> int:
    """Actively suspend the dGPU into D3cold."""
    print(f"\n{BOLD}{CYAN}=== Forcing dGPU into D3cold Sleep ==={RESET}\n")
    from .remediate import suspend_dgpu
    ok, msg, logs = suspend_dgpu(force=args.force)
    for line in logs:
        print(f"  • {line}")
    badge = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    print(f"\n[{badge}] {msg}\n")
    return 0 if ok else 1


def cmd_watch(args: argparse.Namespace) -> int:
    """Run real-time zero-wake monitor."""
    run_monitor(interval=args.interval)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """Quick one-line status output for scripts and status bars."""
    dgpu = find_nvidia_dgpu()
    if not dgpu:
        print("not_found")
        return 1
    s = dgpu.runtime_status
    print(s)
    return 0 if s == "suspended" else 2


def cmd_daemon(_args: argparse.Namespace) -> int:
    """Lightweight user daemon that enforces power/control = auto on dGPU and parent PCIe bridge."""
    dgpu = find_nvidia_dgpu()
    if not dgpu:
        return 1

    ctrl_file = dgpu.sysfs_path / "power" / "control"
    parent_bridge = dgpu.sysfs_path.resolve().parent
    parent_ctrl = parent_bridge / "power" / "control"
    while True:
        try:
            needs_fix = False
            if ctrl_file.exists() and ctrl_file.read_text().strip() != "auto":
                needs_fix = True
            if parent_ctrl.exists() and parent_ctrl.read_text().strip() != "auto":
                needs_fix = True
            if needs_fix:
                apply_live_power_control()
        except OSError:
            pass
        time.sleep(10)
    return 0


def main() -> int:
    cmd_name = "dgpu-doctor" if sys.argv and "dgpu-doctor" in sys.argv[0] else "d3cold"
    parser = argparse.ArgumentParser(
        prog=cmd_name,
        description="Diagnostics, auto-remediation, and zero-wake monitoring for hybrid NVIDIA laptops.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # audit
    subparsers.add_parser("audit", help="Run 5-layer diagnostic audit (default)")

    # suspend
    suspend_p = subparsers.add_parser("suspend", help="Actively suspend dGPU into D3cold (0.0 W)")
    suspend_p.add_argument("-f", "--force", action="store_true", help="Terminate any client processes holding /dev/nvidia*")

    # fix
    fix_p = subparsers.add_parser("fix", help="Apply automated fixes for btop, compositor, and rules")
    fix_p.add_argument("--system", action="store_true", help="Install /etc/udev and /etc/modprobe rules via sudo")
    fix_p.add_argument("--all", action="store_true", help="Apply all fixes including system-wide rules")
    fix_p.add_argument("-f", "--force", action="store_true", help="Terminate any client processes holding /dev/nvidia*")

    # watch
    watch_p = subparsers.add_parser("watch", aliases=["monitor"], help="Passive live monitor with wake detection")
    watch_p.add_argument("-i", "--interval", type=float, default=1.0, help="Sampling interval in seconds (default: 1.0)")

    # status
    subparsers.add_parser("status", help="Print single-word status (suspended/active/unknown)")

    # daemon
    subparsers.add_parser("daemon", help="Background daemon to ensure power/control remains 'auto'")

    args = parser.parse_args()
    if not args.command or args.command == "audit":
        return cmd_audit(args)
    elif args.command == "suspend":
        return cmd_suspend(args)
    elif args.command == "fix":
        return cmd_fix(args)
    elif args.command in ("watch", "monitor"):
        return cmd_watch(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "daemon":
        return cmd_daemon(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
