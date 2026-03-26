"""
Claxon Controller CLI — управление 8 клаксонами (4 ESP × 2 канала) по UDP с mDNS.

Использование:
    python control.py              # найти все ESP и показать список
    python control.py fire 1       # дать сигнал клаксону 1 (esp-1 ch1)
    python control.py fire all     # дать сигнал всем клаксонам
    python control.py fire 3 150   # сигнал 150 мс клаксону 3 (esp-2 ch1)
"""

import sys
import time

from claxon_core import (
    ClaxonSystem, NUM_CLAXONS, CHANNELS_PER_ESP,
    claxon_esp_channel, ping,
)


def main():
    system = ClaxonSystem()
    print("Discovering ESPs...")
    system.start_discovery()
    time.sleep(3)

    devices = system.devices
    if not devices:
        print("No ESPs found!")
        sys.exit(1)

    print(f"\n{len(devices)} ESP(s) found ({len(devices) * CHANNELS_PER_ESP} claxons).\n")

    if len(sys.argv) < 2:
        for i in range(NUM_CLAXONS):
            esp_name, ch = claxon_esp_channel(i)
            esp = devices.get(esp_name)
            if esp:
                ok = ping(esp)
                status = "OK" if ok else "NO RESPONSE"
            else:
                status = "NOT FOUND"
            print(f"  [{i + 1}] {esp_name} ch{ch} — {status}")
        system.stop_discovery()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "fire":
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else None

        if target == "all":
            indices = list(range(NUM_CLAXONS))
        else:
            try:
                num = int(target)
                if 1 <= num <= NUM_CLAXONS:
                    indices = [num - 1]
                else:
                    indices = []
            except ValueError:
                indices = []

        if not indices:
            print(f"Target '{target}' not found.")
            system.stop_discovery()
            sys.exit(1)

        for idx in indices:
            esp_name, ch = claxon_esp_channel(idx)
            result = system.fire(idx, duration)
            if result["success"]:
                piezo = result["piezo"]
                feedback = "SILENT!" if piezo < 50 else f"sound OK (piezo={piezo})"
                print(f"  claxon {idx + 1} ({esp_name} ch{ch}): {feedback}")
            else:
                error = result.get("error", "unknown")
                print(f"  claxon {idx + 1} ({esp_name} ch{ch}): {error}")

    system.stop_discovery()


if __name__ == "__main__":
    main()
