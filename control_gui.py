"""
Claxon Controller GUI — tkinter интерфейс для управления 6 клаксонами.
Управление реальной длительностью звука (20-100 мс) через обратную связь пьезо.
Поддержка воспроизведения MIDI файлов.
Требует: pip install zeroconf mido
"""

import json
import os
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

UDP_PORT = 5000
RECV_TIMEOUT = 1.0
NUM_CLAXONS = 6
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# Маппинг MIDI нот на клаксоны (любая октава)
# C=0, D=1, E=2, F=3, G=4, A=5
NOTE_TO_CLAXON = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


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


def fire_async(device: ClaxonDevice, duration_ms: int):
    """Отправляет FIRE без ожидания ответа (для MIDI playback)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        cmd = f"FIRE:{duration_ms}"
        sock.sendto(cmd.encode(), (device.ip, device.port))
    finally:
        sock.close()


def set_threshold(device: ClaxonDevice, threshold: int) -> bool:
    reply = send_command(device, f"THRESH:{threshold}")
    return reply is not None and reply.startswith("THRESH:")


def fire(device: ClaxonDevice, duration_ms: int) -> dict:
    cmd = f"FIRE:{duration_ms}"
    reply = send_command(device, cmd)
    if not reply:
        return {"success": False, "error": "no_response"}

    if reply.startswith("OK:"):
        parts = reply.split(":")
        return {
            "success": True,
            "piezo": int(parts[1]),
            "startup_delay": int(parts[2]),
            "actual_sound_ms": int(parts[3]),
        }

    if reply.startswith("FAIL:"):
        parts = reply.split(":")
        return {
            "success": False,
            "error": parts[1],
            "piezo": int(parts[2]) if len(parts) > 2 else 0,
        }

    return {"success": False, "error": "unknown"}


class ClaxonPanel(tk.Frame):
    """Панель одного клаксона."""

    NOTE_NAMES = ["C", "D", "E", "F", "G", "A"]

    def __init__(self, parent, index: int, app: "ClaxonApp"):
        super().__init__(parent, relief=tk.GROOVE, borderwidth=2, padx=10, pady=8)
        self.index = index
        self.app = app
        self.device: ClaxonDevice | None = None

        # Заголовок
        self.name_var = tk.StringVar(value=f"claxon-{index + 1}")
        self.status_var = tk.StringVar(value="offline")

        header = tk.Frame(self)
        header.pack(fill=tk.X)

        tk.Label(header, textvariable=self.name_var, font=("Arial", 14, "bold")).pack(side=tk.LEFT)
        tk.Label(header, text=f"[{self.NOTE_NAMES[index]}]", font=("Arial", 10), fg="blue").pack(side=tk.LEFT, padx=4)

        self.status_label = tk.Label(header, textvariable=self.status_var, font=("Arial", 10))
        self.status_label.pack(side=tk.RIGHT)

        # Длительность звука
        dur_frame = tk.Frame(self)
        dur_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Label(dur_frame, text="Sound ms:").pack(side=tk.LEFT)
        self.duration_var = tk.IntVar(value=50)
        self.duration_scale = tk.Scale(
            dur_frame, from_=20, to=100, orient=tk.HORIZONTAL,
            variable=self.duration_var, length=160, showvalue=True
        )
        self.duration_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Порог пьезо
        thresh_frame = tk.Frame(self)
        thresh_frame.pack(fill=tk.X, pady=(4, 0))

        tk.Label(thresh_frame, text="Threshold:").pack(side=tk.LEFT)
        self.threshold_var = tk.IntVar(value=50)
        self.threshold_spin = tk.Spinbox(
            thresh_frame, from_=1, to=1023, increment=10,
            textvariable=self.threshold_var, width=5
        )
        self.threshold_spin.pack(side=tk.LEFT, padx=4)
        self.thresh_btn = tk.Button(thresh_frame, text="Set", command=self.on_set_threshold)
        self.thresh_btn.pack(side=tk.LEFT)

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

        self.detail_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.detail_var, font=("Arial", 9), fg="gray").pack(anchor=tk.W)

        # Индикатор пьезо
        self.piezo_bar = ttk.Progressbar(self, maximum=1023, length=200)
        self.piezo_bar.pack(fill=tk.X, pady=(2, 0))

        self.set_online(False)
        self.load_saved_settings()

    def load_saved_settings(self):
        key = f"claxon-{self.index + 1}"
        saved = self.app.settings.get(key, {})
        if "duration" in saved:
            self.duration_var.set(saved["duration"])
        if "threshold" in saved:
            self.threshold_var.set(saved["threshold"])

    def save_current_settings(self):
        key = f"claxon-{self.index + 1}"
        self.app.settings[key] = {
            "duration": self.duration_var.get(),
            "threshold": self.threshold_var.get(),
        }
        save_settings(self.app.settings)

    def set_device(self, device: ClaxonDevice | None):
        self.device = device
        if device:
            self.name_var.set(device.name)
            self.set_online(True)
            threading.Thread(target=self._sync_threshold, daemon=True).start()
        else:
            self.name_var.set(f"claxon-{self.index + 1}")
            self.set_online(False)

    def _sync_threshold(self):
        if self.device:
            set_threshold(self.device, self.threshold_var.get())

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
            self.detail_var.set("")

    def on_set_threshold(self):
        self.save_current_settings()
        if not self.device:
            self.feedback_var.set(f"Threshold saved locally ({self.threshold_var.get()})")
            return
        self.thresh_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._set_threshold_thread, daemon=True).start()

    def _set_threshold_thread(self):
        ok = set_threshold(self.device, self.threshold_var.get())
        self.after(0, self._set_threshold_done, ok)

    def _set_threshold_done(self, ok: bool):
        self.thresh_btn.config(state=tk.NORMAL)
        if ok:
            self.feedback_var.set(f"Threshold set to {self.threshold_var.get()}")
        else:
            self.feedback_var.set("Threshold: NO RESPONSE (saved locally)")

    def on_fire(self):
        if not self.device:
            return
        self.fire_btn.config(state=tk.DISABLED)
        self.feedback_var.set("...")
        self.detail_var.set("")
        threading.Thread(target=self._fire_thread, daemon=True).start()

    def _fire_thread(self):
        result = fire(self.device, self.duration_var.get())
        self.after(0, self._fire_done, result)

    def _fire_done(self, result: dict):
        self.fire_btn.config(state=tk.NORMAL)
        self.save_current_settings()

        if result["success"]:
            piezo = result["piezo"]
            delay = result["startup_delay"]
            actual = result["actual_sound_ms"]
            self.piezo_bar["value"] = piezo

            if piezo < 50:
                self.feedback_var.set("SILENT!")
            else:
                self.feedback_var.set(f"OK (piezo={piezo})")

            self.detail_var.set(f"delay={delay}ms, sound={actual}ms")
        else:
            error = result.get("error", "unknown")
            self.piezo_bar["value"] = result.get("piezo", 0)
            if error == "no_response":
                self.feedback_var.set("NO RESPONSE")
            elif error == "no_sound":
                self.feedback_var.set("NO SOUND DETECTED")
            else:
                self.feedback_var.set(f"ERROR: {error}")
            self.detail_var.set("")

    def flash(self):
        """Подсветка панели при MIDI note."""
        self.config(bg="#ffcccc")
        self.after(150, lambda: self.config(bg=self._orig_bg))

    @property
    def _orig_bg(self):
        return self.master.cget("bg")


class ClaxonApp:
    def __init__(self):
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title("Claxon Controller")
        self.root.resizable(False, False)

        self.midi_playing = False
        self.midi_stop_event = threading.Event()

        # Верхняя панель
        top = tk.Frame(self.root, padx=10, pady=6)
        top.pack(fill=tk.X)

        self.scan_btn = tk.Button(top, text="Rescan", command=self.start_discovery)
        self.scan_btn.pack(side=tk.LEFT)

        self.info_var = tk.StringVar(value="Scanning...")
        tk.Label(top, textvariable=self.info_var, font=("Arial", 10)).pack(side=tk.LEFT, padx=10)

        # Общая длительность
        tk.Label(top, text="All ms:").pack(side=tk.LEFT, padx=(20, 0))
        self.global_duration = tk.IntVar(value=50)
        global_spin = tk.Spinbox(top, from_=20, to=100, increment=5,
                                 textvariable=self.global_duration, width=5)
        global_spin.pack(side=tk.LEFT, padx=4)

        tk.Button(top, text="Apply to all", command=self.apply_duration_all).pack(side=tk.LEFT)

        # FIRE ALL
        tk.Button(
            top, text="FIRE ALL", font=("Arial", 12, "bold"),
            bg="#cc3333", fg="white", activebackground="#ff4444",
            command=self.fire_all
        ).pack(side=tk.RIGHT)

        # MIDI панель
        midi_frame = tk.Frame(self.root, padx=10, pady=4)
        midi_frame.pack(fill=tk.X)

        tk.Label(midi_frame, text="MIDI:", font=("Arial", 10, "bold")).pack(side=tk.LEFT)

        self.midi_file_var = tk.StringVar(value="no file")
        tk.Label(midi_frame, textvariable=self.midi_file_var, font=("Arial", 9)).pack(side=tk.LEFT, padx=6)

        tk.Button(midi_frame, text="Load", command=self.load_midi).pack(side=tk.LEFT, padx=2)

        self.midi_play_btn = tk.Button(midi_frame, text="Play", command=self.play_midi, state=tk.DISABLED)
        self.midi_play_btn.pack(side=tk.LEFT, padx=2)

        self.midi_stop_btn = tk.Button(midi_frame, text="Stop", command=self.stop_midi, state=tk.DISABLED)
        self.midi_stop_btn.pack(side=tk.LEFT, padx=2)

        self.midi_status_var = tk.StringVar(value="")
        tk.Label(midi_frame, textvariable=self.midi_status_var, font=("Arial", 9), fg="gray").pack(side=tk.LEFT, padx=6)

        # C D E F G A
        tk.Label(midi_frame, text="Notes: C D E F G A", font=("Arial", 9), fg="blue").pack(side=tk.RIGHT)

        # Сетка клаксонов 2x3
        grid = tk.Frame(self.root, padx=10, pady=6)
        grid.pack(fill=tk.BOTH)

        self.panels: list[ClaxonPanel] = []
        for i in range(NUM_CLAXONS):
            panel = ClaxonPanel(grid, i, self)
            row, col = divmod(i, 3)
            panel.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            self.panels.append(panel)

        for c in range(3):
            grid.columnconfigure(c, weight=1)

        # mDNS
        self.zc: Zeroconf | None = None
        self.listener: ClaxonDiscovery | None = None
        self.midi_data = None
        self.start_discovery()

    def start_discovery(self):
        self.stop_discovery()
        self.info_var.set("Scanning...")
        self.zc = Zeroconf()
        self.listener = ClaxonDiscovery(callback=self._on_devices_changed)
        ServiceBrowser(self.zc, "_claxon._udp.local.", self.listener)
        self.root.after(3000, self._update_panels)

    def stop_discovery(self):
        if self.zc:
            self.zc.close()
            self.zc = None

    def _on_devices_changed(self):
        self.root.after(100, self._update_panels)

    def _update_panels(self):
        if not self.listener:
            return

        devices = self.listener.devices
        sorted_names = sorted(devices.keys())

        for i, panel in enumerate(self.panels):
            expected = f"claxon-{i + 1}"
            if expected in devices:
                panel.set_device(devices[expected])
            elif i < len(sorted_names):
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

    # --- MIDI ---

    def load_midi(self):
        path = filedialog.askopenfilename(
            title="Select MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            import mido
            mid = mido.MidiFile(path)
        except ImportError:
            self.midi_status_var.set("pip install mido")
            return
        except Exception as e:
            self.midi_status_var.set(f"Error: {e}")
            return

        # Парсим MIDI в список событий (time_sec, claxon_index, duration_ms)
        events = []
        for track in mid.tracks:
            abs_time = 0
            note_on_times: dict[int, float] = {}
            for msg in track:
                abs_time += mido.tick2second(msg.time, mid.ticks_per_beat, self._get_tempo(mid))
                if msg.type == "note_on" and msg.velocity > 0:
                    note_class = msg.note % 12
                    if note_class in NOTE_TO_CLAXON:
                        note_on_times[msg.note] = abs_time
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    note_class = msg.note % 12
                    if msg.note in note_on_times and note_class in NOTE_TO_CLAXON:
                        start = note_on_times.pop(msg.note)
                        dur_ms = int((abs_time - start) * 1000)
                        dur_ms = max(20, min(100, dur_ms))
                        claxon_idx = NOTE_TO_CLAXON[note_class]
                        events.append((start, claxon_idx, dur_ms))

        events.sort(key=lambda e: e[0])
        self.midi_data = events

        filename = os.path.basename(path)
        self.midi_file_var.set(filename)
        self.midi_status_var.set(f"{len(events)} notes")
        self.midi_play_btn.config(state=tk.NORMAL)

    def _get_tempo(self, mid):
        """Получает темп из MIDI файла."""
        import mido
        for track in mid.tracks:
            for msg in track:
                if msg.type == "set_tempo":
                    return msg.tempo
        return mido.bpm2tempo(120)

    def play_midi(self):
        if not self.midi_data or self.midi_playing:
            return
        self.midi_playing = True
        self.midi_stop_event.clear()
        self.midi_play_btn.config(state=tk.DISABLED)
        self.midi_stop_btn.config(state=tk.NORMAL)
        self.midi_status_var.set("Playing...")
        threading.Thread(target=self._midi_playback_thread, daemon=True).start()

    def stop_midi(self):
        self.midi_stop_event.set()

    def _midi_playback_thread(self):
        events = self.midi_data
        start_time = time.time()

        for i, (event_time, claxon_idx, dur_ms) in enumerate(events):
            if self.midi_stop_event.is_set():
                break

            # Ждём нужный момент
            target = start_time + event_time
            now = time.time()
            if target > now:
                wait = target - now
                if self.midi_stop_event.wait(timeout=wait):
                    break

            # Отправляем FIRE
            panel = self.panels[claxon_idx]
            if panel.device:
                fire_async(panel.device, dur_ms)

            # Подсветка в GUI
            self.root.after(0, panel.flash)
            self.root.after(0, lambda idx=i: self.midi_status_var.set(
                f"Playing... {idx + 1}/{len(events)}"
            ))

        self.root.after(0, self._midi_playback_done)

    def _midi_playback_done(self):
        self.midi_playing = False
        self.midi_play_btn.config(state=tk.NORMAL)
        self.midi_stop_btn.config(state=tk.DISABLED)
        if self.midi_stop_event.is_set():
            self.midi_status_var.set("Stopped")
        else:
            self.midi_status_var.set("Done")

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.midi_stop_event.set()
            self.stop_discovery()


if __name__ == "__main__":
    ClaxonApp().run()
