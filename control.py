"""
Claxon Controller — управление клаксонами по UDP с обнаружением через mDNS.

Использование:
    python control.py              # найти все клаксоны и показать список
    python control.py fire 1       # дать сигнал клаксону 1
    python control.py fire all     # дать сигнал всем клаксонам
    python control.py fire 1 150   # сигнал 150 мс клаксону 1
"""

import socket
import time
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

UDP_PORT = 5000
RECV_TIMEOUT = 0.5  # секунд ожидания ответа


class ClaxonDevice:
    def __init__(self, name: str, ip: str, port: int):
        self.name = name
        self.ip = ip
        self.port = port

    def __repr__(self):
        return f"{self.name} ({self.ip}:{self.port})"


class ClaxonDiscovery(ServiceListener):
    """Обнаружение клаксонов в сети через mDNS."""

    def __init__(self):
        self.devices: dict[str, ClaxonDevice] = {}

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
            dev = ClaxonDevice(short_name, ip, port)
            self.devices[short_name] = dev
            print(f"  Found: {dev}")


def discover(timeout=3.0) -> dict[str, ClaxonDevice]:
    """Ищет клаксоны в сети."""
    print("Discovering claxons...")
    zc = Zeroconf()
    listener = ClaxonDiscovery()
    ServiceBrowser(zc, "_claxon._udp.local.", listener)
    time.sleep(timeout)
    zc.close()
    return listener.devices


def send_command(device: ClaxonDevice, command: str) -> str | None:
    """Отправляет UDP команду и ждёт ответ."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(RECV_TIMEOUT)
    try:
        sock.sendto(command.encode(), (device.ip, device.port))
        data, _ = sock.recvfrom(256)
        return data.decode()
    except socket.timeout:
        return None
    finally:
        sock.close()


def fire(device: ClaxonDevice, duration_ms: int = 100) -> tuple[bool, int]:
    """
    Даёт сигнал клаксону.
    Возвращает (success, piezo_peak).
    """
    cmd = f"FIRE:{duration_ms}" if duration_ms != 100 else "FIRE"
    reply = send_command(device, cmd)
    if reply and reply.startswith("OK:"):
        piezo = int(reply.split(":")[1])
        return True, piezo
    return False, 0


def ping(device: ClaxonDevice) -> bool:
    """Проверяет связь с клаксоном."""
    reply = send_command(device, "PING")
    return reply is not None and reply.startswith("PONG")


# --- CLI ---
if __name__ == "__main__":
    import sys

    devices = discover()
    if not devices:
        print("No claxons found!")
        sys.exit(1)

    print(f"\n{len(devices)} claxon(s) found.\n")

    if len(sys.argv) < 2:
        # Просто показать список
        for i, (name, dev) in enumerate(sorted(devices.items()), 1):
            ok = ping(dev)
            status = "OK" if ok else "NO RESPONSE"
            print(f"  [{i}] {dev} — {status}")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "fire":
        target = sys.argv[2] if len(sys.argv) > 2 else "all"
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else 100

        targets = []
        if target == "all":
            targets = list(devices.values())
        else:
            # По номеру или имени
            sorted_devs = sorted(devices.items())
            try:
                idx = int(target) - 1
                targets = [sorted_devs[idx][1]]
            except (ValueError, IndexError):
                matching = [d for d in devices.values() if target in d.name]
                targets = matching

        if not targets:
            print(f"Target '{target}' not found.")
            sys.exit(1)

        for dev in targets:
            success, piezo = fire(dev, duration)
            if success:
                feedback = "SILENT!" if piezo < 50 else f"sound OK (piezo={piezo})"
                print(f"  {dev.name}: {feedback}")
            else:
                print(f"  {dev.name}: NO RESPONSE")
