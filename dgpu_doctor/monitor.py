"""Zero-wake passive real-time monitor and wakeup culprit detector.

CRITICAL:
The monitoring loop must NEVER query tools or files that wake the dGPU.
It only inspects Linux kernel PCI sysfs (`runtime_status`).
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from .pci import (
    find_nvidia_dgpu,
    get_battery_energy_rate,
    get_cpu_package_power,
    get_nvidia_client_processes,
)


def run_monitor(interval: float = 1.0, duration: float | None = None) -> None:
    """Run an interactive console monitor tracking dGPU state and power draw."""
    dgpu = find_nvidia_dgpu()
    if not dgpu:
        print("Error: No discrete NVIDIA GPU detected on PCI bus.")
        return

    print("=================================================================")
    print(" d3cold - Zero-Wake Live Monitor")
    print(f" Target: {dgpu.name} ({dgpu.pci_address})")
    print(" Monitoring PCI runtime_status passively without waking device...")
    print(" Press Ctrl+C to exit.")
    print("=================================================================\n")

    last_status = dgpu.runtime_status
    start_time = time.time()
    wake_start: float | None = None

    try:
        while True:
            now_str = datetime.now().strftime("%H:%M:%S")
            current_status = dgpu.runtime_status
            watts, bat_status = get_battery_energy_rate()
            cpu_w = get_cpu_package_power(0.2)

            if watts is not None:
                if bat_status.lower() == "charging":
                    watt_str = f" | Bat: +{watts:.1f}W (Charging)"
                elif bat_status.lower() == "full":
                    watt_str = " | AC (Full)"
                else:
                    watt_str = f" | Battery: {watts:.1f}W"
            else:
                watt_str = " | AC"
            cpu_str = f" | CPU Pkg: {cpu_w:.1f}W" if cpu_w is not None else ""

            # Check for transitions
            p_state = dgpu.power_state
            if current_status != last_status:
                if current_status == "active" and last_status == "suspended":
                    # WAKE EVENT DETECTED!
                    wake_start = time.time()
                    clients = get_nvidia_client_processes()
                    culprit_info = ""
                    if clients:
                        procs = ", ".join(f"{c['comm']} (PID {c['pid']})" for c in clients)
                        culprit_info = f" -> Culprit: {procs}"
                    else:
                        culprit_info = " -> Culprit: Background probe or kernel event"

                    print(f"\n[{now_str}] ⚠️  WAKEUP: suspended -> active{culprit_info}")
                elif current_status == "suspended" and last_status in ("active", "suspending"):
                    elapsed_str = ""
                    if wake_start:
                        elapsed = time.time() - wake_start
                        elapsed_str = f" (after {elapsed:.1f}s active)"
                    print(f"\n[{now_str}] 💤 SLEEP: returned to {p_state} ({current_status}){elapsed_str}\n")
                last_status = current_status

            # Format status badge
            if current_status == "suspended":
                if p_state == "D3cold":
                    status_badge = "\033[92m● D3cold (0.0W)\033[0m"
                else:
                    status_badge = f"\033[93m● {p_state} (~14W)\033[0m"
            elif current_status == "suspending":
                status_badge = "\033[93m◐ SUSPENDING...\033[0m"
            elif current_status == "resuming":
                status_badge = "\033[93m◑ RESUMING...\033[0m"
            else:
                status_badge = "\033[91m● ACTIVE\033[0m"

            sys.stdout.write(f"\r[{now_str}] dGPU: {status_badge}{watt_str}{cpu_str}    ")
            sys.stdout.flush()

            if duration and (time.time() - start_time) >= duration:
                break
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n\nMonitor stopped.")
