"""Zero-wake passive real-time monitor and wakeup culprit detector."""

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
    dgpu = find_nvidia_dgpu()
    if not dgpu:
        print("Error: No discrete NVIDIA GPU detected on PCI bus.")
        return

    print("=" * 65)
    print(f" d3cold - Zero-Wake Live Monitor | Target: {dgpu.name} ({dgpu.pci_address})")
    print(" Monitoring PCI runtime_status passively without waking device... (Ctrl+C to exit)")
    print("=" * 65 + "\n")

    last_status, start_time, wake_start = dgpu.runtime_status, time.time(), None
    try:
        while True:
            now_str = datetime.now().strftime("%H:%M:%S")
            cur_status = dgpu.runtime_status
            watts, bat_status = get_battery_energy_rate()
            cpu_w = get_cpu_package_power(0.2)

            watt_str = f" | Bat: +{watts:.1f}W (Charging)" if bat_status.lower() == "charging" and watts else (f" | Battery: {watts:.1f}W" if watts else (" | AC (Full)" if bat_status.lower() == "full" else " | AC"))
            cpu_str = f" | CPU: {cpu_w:.1f}W" if cpu_w is not None else ""

            if cur_status != last_status:
                if cur_status == "active" and last_status == "suspended":
                    wake_start = time.time()
                    clients = get_nvidia_client_processes()
                    procs = ", ".join(f"{c['comm']} (PID {c['pid']})" for c in clients) if clients else "Kernel/driver probe"
                    print(f"\n[{now_str}] ⚠️  WAKEUP: suspended -> active -> Culprit: {procs}")
                elif cur_status == "suspended" and last_status in ("active", "suspending"):
                    elapsed = f" (after {time.time() - wake_start:.1f}s active)" if wake_start else ""
                    print(f"\n[{now_str}] 💤 SLEEP: returned to {dgpu.power_state} ({cur_status}){elapsed}\n")
                last_status = cur_status

            badges = {
                "suspended": "\033[92m● D3cold (0.0W)\033[0m" if dgpu.power_state == "D3cold" else f"\033[93m● {dgpu.power_state} (~14W)\033[0m",
                "suspending": "\033[93m◐ SUSPENDING...\033[0m",
                "resuming": "\033[93m◑ RESUMING...\033[0m",
            }
            badge = badges.get(cur_status, "\033[91m● ACTIVE\033[0m")
            sys.stdout.write(f"\r[{now_str}] dGPU: {badge}{watt_str}{cpu_str}    ")
            sys.stdout.flush()

            if duration and (time.time() - start_time) >= duration:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nExiting monitor.")
