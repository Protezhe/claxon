"""
Claxon Controller — управление 8 клаксонами (4 ESP × 2 канала) по UDP с mDNS.

Использование:
    python control.py              # найти все ESP и показать список
    python control.py fire 1       # дать сигнал клаксону 1 (esp-1 ch1)
    python control.py fire all     # дать сигнал всем клаксонам
    python control.py fire 3 150   # сигнал 150 мс клаксону 3 (esp-2 ch1)
"""

import socket
import time
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

UDP_PORT = 5000
RECV_TIMEOUT = 0.5
NUM_ESPS = 4
CHANNELS_PER_ESP = 2
NUM_CLAXONS = NUM_ESPS * CHANNELS_PER_ESP  # 8


class EspDevice:
    def __init__(self, name: str, ip: str, port: int):
        self.name = name
        self.ip = ip
        self.port = port

    def __repr__(self):
        return f"{self.name} ({self.ip}:{self.port})"


class ClaxonDiscovery(ServiceListener):
    def __init__(self):
        self.devices: dict[str, EspDevice] = {}

    def update_service(self, zc, type_, name):
        pass

    def remove_service(self, zc, type_, name):
        short_name = name.split(".")[0]
        self.devices.pop(short_name, None)

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port
            short_name = name.split(".")[0]
            dev = EspDevice(short_name, ip, port)
            self.devices[short_name] = dev
            print(f"  Found: {dev}")


def discover(timeout=3.0) -> dict[str, EspDevice]:
    print("Discovering ESPs...")
    zc = Zeroconf()
    listener = ClaxonDiscovery()
    ServiceBrowser(zc, "_claxon._udp.local.", listener)
    time.sleep(timeout)
    zc.close()
    return listener.devices


def send_command(esp: EspDevice, command: str) -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(RECV_TIMEOUT)
    try:
        sock.sendto(command.encode(), (esp.ip, esp.port))
        data, _ = sock.recvfrom(256)
        return data.decode()
    except socket.timeout:
        return None
    finally:
        sock.close()


def fire(esp: EspDevice, channel: int, duration_ms: int = 100) -> tuple[bool, int]:
    cmd = f"FIRE:{channel}:{duration_ms}"
    reply = send_command(esp, cmd)
    if reply and reply.startswith("OK:"):
        parts = reply.split(":")
        piezo = int(parts[2])
        return True, piezo
    return False, 0


def ping(esp: EspDevice) -> bool:
    reply = send_command(esp, "PING")
    return reply is not None and reply.startswith("PONG")


def claxon_to_esp(claxon_num: int) -> tuple[str, int]:
    """Клаксон 1-8 → (esp_name, channel 1-2)."""
    esp_idx = (claxon_num - 1) // CHANNELS_PER_ESP
    channel = (claxon_num - 1) % CHANNELS_PER_ESP + 1
    return f"esp-{esp_idx + 1}", channel


# --- CLI ---
if __name__ == "__main__":
    import sys

    devices = discover()
    if not devices:
        print("No ESPs found!")
        sys.exit(1)

    print(f"\n{len(devices)} ESP(s) found ({len(devices) * CHANNELS_PER_ESP} claxons).\n")

    if len(sys.argv) < 2:
        for i in range(1, NUM_CLAXONS + 1):
            esp_name, ch = claxon_to_esp(i)
            esp = devices.get(esp_name)
            if esp:
                ok = ping(esp)
                status = "OK" if ok else "NO RESPONSE"
            else:
                status = "NOT FOUND"
            print(f"  [{i}] {esp_name} ch{ch} — {status}")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "fire":
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else 100

        targets: list[tuple[EspDevice, int, int]] = []  # (esp, channel, claxon_num)

        if target == "all":
            for i in range(1, NUM_CLAXONS + 1):
                esp_name, ch = claxon_to_esp(i)
                esp = devices.get(esp_name)
                if esp:
                    targets.append((esp, ch, i))
        else:
            try:
                num = int(target)
                if 1 <= num <= NUM_CLAXONS:
                    esp_name, ch = claxon_to_esp(num)
                    esp = devices.get(esp_name)
                    if esp:
                        targets.append((esp, ch, num))
            except ValueError:
                pass

        if not targets:
            print(f"Target '{target}' not found.")
            sys.exit(1)

        for esp, ch, num in targets:
            success, piezo = fire(esp, ch, duration)
            if success:
                feedback = "SILENT!" if piezo < 50 else f"sound OK (piezo={piezo})"
                print(f"  claxon {num} ({esp.name} ch{ch}): {feedback}")
            else:
                print(f"  claxon {num} ({esp.name} ch{ch}): NO RESPONSE")
