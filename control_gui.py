"""
Claxon Controller GUI — tkinter интерфейс для управления 6 клаксонами.
Требует: pip install zeroconf
"""

import socket
import threading
import tkinter as tk
from tkinter import ttk
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

UDP_PORT = 5000
RECV_TIMEOUT = 0.5
NUM_CLAXONS = 6


class ClaxonDevice:
    def __init__(self, name: str, ip: str, port: int):
        self.name = name
        self.ip = ip
        self.port = port


class ClaxonDiscovery(ServiceListener):
    def __init__(self, callback):
        self.devices: dict[str, ClaxonDevice] = {}
        self.callback = callback

    def update_service(self, zc, type_, name):
        pass

    def remove_service(self, zc, type_, name):
        short_name = name.split(".")[0]
        self.devices.pop(short_name, None)
        self.callback()

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port
            short_name = name.split(".")[0]
            self.devices[short_name] = ClaxonDevice(short_name, ip, port)
            self.callback()


def send_command(device: ClaxonDevice, command: str) -> str | None:
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


def fire(device: ClaxonDevice, duration_ms: int) -> tuple[bool, int]:
    cmd = f"FIRE:{duration_ms}" if duration_ms != 100 else "FIRE"
    reply = send_command(device, cmd)
    if reply and reply.startswith("OK:"):
        piezo = int(reply.split(":")[1])
        return True, piezo
    return False, 0


class ClaxonPanel(tk.Frame):
    """Панель одного клаксона."""

    def __init__(self, parent, index: int):
        super().__init__(parent, relief=tk.GROOVE, borderwidth=2, padx=10, pady=8)
        self.index = index
        self.device: ClaxonDevice | None = None

        # Заголовок
        self.name_var = tk.StringVar(value=f"claxon-{index + 1}")
        self.status_var = tk.StringVar(value="offline")

        header = tk.Frame(self)
        header.pack(fill=tk.X)

        tk.Label(header, textvariable=self.name_var, font=("Arial", 14, "bold")).pack(side=tk.LEFT)

        self.status_label = tk.Label(header, textvariable=self.status_var, font=("Arial", 10))
        self.status_label.pack(side=tk.RIGHT)

        # Длительность
        dur_frame = tk.Frame(self)
        dur_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Label(dur_frame, text="ms:").pack(side=tk.LEFT)
        self.duration_var = tk.IntVar(value=100)
        self.duration_scale = tk.Scale(
            dur_frame, from_=50, to=500, orient=tk.HORIZONTAL,
            variable=self.duration_var, length=180, showvalue=True
        )
        self.duration_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Кнопка FIRE
        self.fire_btn = tk.Button(
            self, text="FIRE", font=("Arial", 16, "bold"),
            bg="#cc3333", fg="white", activebackground="#ff4444",
            height=1, command=self.on_fire
        )
        self.fire_btn.pack(fill=tk.X, pady=(6, 0))

        # Обратная связь
        self.feedback_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.feedback_var, font=("Arial", 10)).pack(anchor=tk.W, pady=(4, 0))

        # Индикатор пьезо
        self.piezo_bar = ttk.Progressbar(self, maximum=1023, length=200)
        self.piezo_bar.pack(fill=tk.X, pady=(2, 0))

        self.set_online(False)

    def set_device(self, device: ClaxonDevice | None):
        self.device = device
        if device:
            self.name_var.set(device.name)
            self.set_online(True)
        else:
            self.name_var.set(f"claxon-{self.index + 1}")
            self.set_online(False)

    def set_online(self, online: bool):
        if online:
            self.status_var.set("online")
            self.status_label.config(fg="green")
            self.fire_btn.config(state=tk.NORMAL)
        else:
            self.status_var.set("offline")
            self.status_label.config(fg="gray")
            self.fire_btn.config(state=tk.DISABLED)
            self.piezo_bar["value"] = 0
            self.feedback_var.set("")

    def on_fire(self):
        if not self.device:
            return
        self.fire_btn.config(state=tk.DISABLED)
        self.feedback_var.set("...")
        threading.Thread(target=self._fire_thread, daemon=True).start()

    def _fire_thread(self):
        success, piezo = fire(self.device, self.duration_var.get())
        self.after(0, self._fire_done, success, piezo)

    def _fire_done(self, success: bool, piezo: int):
        self.fire_btn.config(state=tk.NORMAL)
        if success:
            self.piezo_bar["value"] = piezo
            if piezo < 50:
                self.feedback_var.set("SILENT!")
            else:
                self.feedback_var.set(f"sound OK (piezo={piezo})")
        else:
            self.feedback_var.set("NO RESPONSE")
            self.piezo_bar["value"] = 0


class ClaxonApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Claxon Controller")
        self.root.resizable(False, False)

        # Верхняя панель
        top = tk.Frame(self.root, padx=10, pady=6)
        top.pack(fill=tk.X)

        self.scan_btn = tk.Button(top, text="Rescan", command=self.start_discovery)
        self.scan_btn.pack(side=tk.LEFT)

        self.info_var = tk.StringVar(value="Scanning...")
        tk.Label(top, textvariable=self.info_var, font=("Arial", 10)).pack(side=tk.LEFT, padx=10)

        # Общая длительность
        tk.Label(top, text="All ms:").pack(side=tk.LEFT, padx=(20, 0))
        self.global_duration = tk.IntVar(value=100)
        global_spin = tk.Spinbox(top, from_=50, to=500, increment=10,
                                 textvariable=self.global_duration, width=5)
        global_spin.pack(side=tk.LEFT, padx=4)

        tk.Button(top, text="Apply to all", command=self.apply_duration_all).pack(side=tk.LEFT)

        # FIRE ALL
        tk.Button(
            top, text="FIRE ALL", font=("Arial", 12, "bold"),
            bg="#cc3333", fg="white", activebackground="#ff4444",
            command=self.fire_all
        ).pack(side=tk.RIGHT)

        # Сетка клаксонов 2x3
        grid = tk.Frame(self.root, padx=10, pady=6)
        grid.pack(fill=tk.BOTH)

        self.panels: list[ClaxonPanel] = []
        for i in range(NUM_CLAXONS):
            panel = ClaxonPanel(grid, i)
            row, col = divmod(i, 3)
            panel.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            self.panels.append(panel)

        for c in range(3):
            grid.columnconfigure(c, weight=1)

        # mDNS
        self.zc: Zeroconf | None = None
        self.listener: ClaxonDiscovery | None = None
        self.start_discovery()

    def start_discovery(self):
        self.stop_discovery()
        self.info_var.set("Scanning...")
        self.zc = Zeroconf()
        self.listener = ClaxonDiscovery(callback=self._on_devices_changed)
        ServiceBrowser(self.zc, "_claxon._udp.local.", self.listener)
        # Обновить UI через 3 секунды
        self.root.after(3000, self._update_panels)

    def stop_discovery(self):
        if self.zc:
            self.zc.close()
            self.zc = None

    def _on_devices_changed(self):
        # Вызывается из потока zeroconf — планируем обновление в main thread
        self.root.after(100, self._update_panels)

    def _update_panels(self):
        if not self.listener:
            return

        devices = self.listener.devices
        # Сортируем по имени и раскладываем по панелям
        sorted_names = sorted(devices.keys())

        for i, panel in enumerate(self.panels):
            expected = f"claxon-{i + 1}"
            if expected in devices:
                panel.set_device(devices[expected])
            elif i < len(sorted_names):
                # Если имена не по порядку — заполняем что есть
                panel.set_device(devices[sorted_names[i]])
            else:
                panel.set_device(None)

        online = sum(1 for p in self.panels if p.device)
        self.info_var.set(f"{online}/{NUM_CLAXONS} online")

    def apply_duration_all(self):
        val = self.global_duration.get()
        for panel in self.panels:
            panel.duration_var.set(val)

    def fire_all(self):
        for panel in self.panels:
            if panel.device:
                panel.on_fire()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.stop_discovery()


if __name__ == "__main__":
    ClaxonApp().run()
