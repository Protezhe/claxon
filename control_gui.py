"""
Claxon Controller GUI — tkinter интерфейс для настройки клаксонов.
4 ESP × 2 канала = 8 клаксонов.
Настройка: длительность, порог пьезо, мощность ШИМ, MIDI нота.
Тестовый FIRE и MIDI playback с калибровкой.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

from claxon_core import (
    ClaxonSystem, ALL_NOTE_NAMES, DEFAULT_NOTES,
    NUM_ESPS, CHANNELS_PER_ESP, NUM_CLAXONS,
    load_settings, save_settings,
)


class ClaxonPanel(tk.Frame):
    """Панель одного клаксона."""

    def __init__(self, parent, index: int, app: "ClaxonApp"):
        super().__init__(parent, relief=tk.GROOVE, borderwidth=2, padx=10, pady=8)
        self.index = index
        self.app = app
        self.channel: int = (index % CHANNELS_PER_ESP) + 1

        # Заголовок
        esp_num = (index // CHANNELS_PER_ESP) + 1
        self.name_var = tk.StringVar(value=f"esp-{esp_num} ch{self.channel}")
        self.status_var = tk.StringVar(value="offline")

        header = tk.Frame(self)
        header.pack(fill=tk.X)

        tk.Label(header, textvariable=self.name_var, font=("Arial", 14, "bold")).pack(side=tk.LEFT)

        # MIDI note selector
        cfg = self.app.system.get_claxon_config(index)
        self.note_var = tk.StringVar(value=ALL_NOTE_NAMES[cfg["note"]])
        self.note_combo = ttk.Combobox(
            header, textvariable=self.note_var, values=ALL_NOTE_NAMES,
            width=3, state="readonly"
        )
        self.note_combo.pack(side=tk.LEFT, padx=4)
        self.note_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_settings())

        self.status_label = tk.Label(header, textvariable=self.status_var, font=("Arial", 10))
        self.status_label.pack(side=tk.RIGHT)

        # Длительность звука
        dur_frame = tk.Frame(self)
        dur_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Label(dur_frame, text="Sound ms:").pack(side=tk.LEFT)
        self.duration_var = tk.IntVar(value=cfg["duration"])
        self.duration_scale = tk.Scale(
            dur_frame, from_=20, to=1000, orient=tk.HORIZONTAL,
            variable=self.duration_var, length=160, showvalue=True
        )
        self.duration_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Порог пьезо
        thresh_frame = tk.Frame(self)
        thresh_frame.pack(fill=tk.X, pady=(4, 0))

        tk.Label(thresh_frame, text="Threshold:").pack(side=tk.LEFT)
        self.threshold_var = tk.IntVar(value=cfg["threshold"])
        self.threshold_spin = tk.Spinbox(
            thresh_frame, from_=1, to=1023, increment=10,
            textvariable=self.threshold_var, width=5
        )
        self.threshold_spin.pack(side=tk.LEFT, padx=4)
        self.thresh_btn = tk.Button(thresh_frame, text="Set", command=self.on_set_threshold)
        self.thresh_btn.pack(side=tk.LEFT)

        # Мощность ШИМ
        pwr_frame = tk.Frame(self)
        pwr_frame.pack(fill=tk.X, pady=(4, 0))

        tk.Label(pwr_frame, text="Power %:").pack(side=tk.LEFT)
        self.power_var = tk.DoubleVar(value=cfg["power"])
        self.power_scale = tk.Scale(
            pwr_frame, from_=0, to=100, orient=tk.HORIZONTAL,
            variable=self.power_var, length=200, showvalue=True,
            resolution=0.1, digits=4
        )
        self.power_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.power_btn = tk.Button(pwr_frame, text="Set", command=self.on_set_power)
        self.power_btn.pack(side=tk.LEFT, padx=4)

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

    def _get_config(self) -> dict:
        note_name = self.note_var.get()
        note_idx = ALL_NOTE_NAMES.index(note_name) if note_name in ALL_NOTE_NAMES else 0
        return {
            "duration": self.duration_var.get(),
            "threshold": self.threshold_var.get(),
            "power": self.power_var.get(),
            "note": note_idx,
        }

    def save_current_settings(self):
        self.app.system.set_claxon_config(self.index, self._get_config())

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

    def update_esp_status(self):
        system = self.app.system
        online = system.is_online(self.index)
        if online:
            esp = system.get_esp_for_claxon(self.index)
            self.name_var.set(f"{esp.name} ch{self.channel}")
        else:
            esp_num = (self.index // CHANNELS_PER_ESP) + 1
            self.name_var.set(f"esp-{esp_num} ch{self.channel}")
        self.set_online(online)

    def on_set_threshold(self):
        self.save_current_settings()
        if not self.app.system.is_online(self.index):
            self.feedback_var.set(f"Threshold saved locally ({self.threshold_var.get()})")
            return
        self.thresh_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._set_threshold_thread, daemon=True).start()

    def _set_threshold_thread(self):
        self.app.system.sync_claxon_to_esp(self.index)
        self.after(0, lambda: self.thresh_btn.config(state=tk.NORMAL))
        self.after(0, lambda: self.feedback_var.set(f"Threshold set to {self.threshold_var.get()}"))

    def on_set_power(self):
        self.save_current_settings()
        if not self.app.system.is_online(self.index):
            self.feedback_var.set(f"Power saved locally ({self.power_var.get()}%)")
            return
        self.power_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._set_power_thread, daemon=True).start()

    def _set_power_thread(self):
        self.app.system.sync_claxon_to_esp(self.index)
        self.after(0, lambda: self.power_btn.config(state=tk.NORMAL))
        self.after(0, lambda: self.feedback_var.set(f"Power set to {self.power_var.get()}%"))

    def on_fire(self):
        if not self.app.system.is_online(self.index):
            return
        self.fire_btn.config(state=tk.DISABLED)
        self.feedback_var.set("...")
        self.detail_var.set("")
        threading.Thread(target=self._fire_thread, daemon=True).start()

    def _fire_thread(self):
        result = self.app.system.fire(self.index, self.duration_var.get())
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
            elif error == "busy":
                self.feedback_var.set("BUSY (другой канал играет)")
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
        self.system = ClaxonSystem()

        self.root = tk.Tk()
        self.root.title("Claxon Controller — 4 ESP × 2 ch = 8 claxons")
        self.root.resizable(False, False)

        self.midi_playing = False
        self.midi_stop_event = threading.Event()
        self.calibration_map: list[int] = []

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
        global_spin = tk.Spinbox(top, from_=20, to=1000, increment=10,
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

        tk.Button(midi_frame, text="Reset Cal", command=self.reset_calibration).pack(side=tk.LEFT, padx=6)

        self.midi_status_var = tk.StringVar(value="")
        tk.Label(midi_frame, textvariable=self.midi_status_var, font=("Arial", 9), fg="gray").pack(side=tk.LEFT, padx=6)

        self.midi_note_label_var = tk.StringVar(value="")
        tk.Label(midi_frame, textvariable=self.midi_note_label_var, font=("Arial", 9), fg="blue").pack(side=tk.RIGHT)

        # Сетка клаксонов 2x4
        grid = tk.Frame(self.root, padx=10, pady=6)
        grid.pack(fill=tk.BOTH)

        self.panels: list[ClaxonPanel] = []
        for i in range(NUM_CLAXONS):
            panel = ClaxonPanel(grid, i, self)
            row, col = divmod(i, 4)
            panel.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            self.panels.append(panel)

        for c in range(4):
            grid.columnconfigure(c, weight=1)

        self._update_note_label()

        # mDNS
        self.midi_data = None
        self.midi_filename = None
        self.system.set_on_change(lambda: self.root.after(100, self._update_panels))
        self.start_discovery()

        # Автозагрузка последнего MIDI файла
        last_midi = self.system.settings.get("last_midi")
        if last_midi and os.path.isfile(last_midi):
            self._load_midi_file(last_midi)

    def _update_note_label(self):
        self.midi_note_label_var.set(" ".join(self.system.note_names_list()))

    def start_discovery(self):
        self.info_var.set("Scanning...")
        self.system.start_discovery()
        self.root.after(3000, self._update_panels)

    def _update_panels(self):
        for panel in self.panels:
            panel.update_esp_status()

        online_esps = sum(1 for i in range(NUM_ESPS) if f"esp-{i + 1}" in self.system.devices)
        online_claxons = online_esps * CHANNELS_PER_ESP
        self.info_var.set(f"{online_esps} ESP ({online_claxons}/{NUM_CLAXONS} claxons)")

    def apply_duration_all(self):
        val = self.global_duration.get()
        for panel in self.panels:
            panel.duration_var.set(val)

    def fire_all(self):
        for panel in self.panels:
            if self.system.is_online(panel.index):
                panel.on_fire()

    # --- Calibration ---

    def reset_calibration(self):
        self.calibration_map = []
        if self.midi_filename:
            self.system.settings.pop(f"cal:{self.midi_filename}", None)
            save_settings(self.system.settings)
        self.midi_status_var.set("Calibration reset")

    # --- MIDI ---

    def load_midi(self):
        path = filedialog.askopenfilename(
            title="Select MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")]
        )
        if not path:
            return
        self._load_midi_file(path)

    def _load_midi_file(self, path: str):
        try:
            events = self.system.parse_midi(path)
        except ImportError:
            self.midi_status_var.set("pip install mido")
            return
        except Exception as e:
            self.midi_status_var.set(f"Error: {e}")
            return

        self.midi_data = events
        self._update_note_label()

        filename = os.path.basename(path)
        self.midi_filename = filename

        self.system.settings["last_midi"] = path
        save_settings(self.system.settings)

        cal_key = f"cal:{filename}"
        saved_cal = self.system.settings.get(cal_key, [])
        if isinstance(saved_cal, list) and len(saved_cal) == len(events):
            self.calibration_map = saved_cal
        else:
            self.calibration_map = []

        self.midi_file_var.set(filename)
        cal_info = ", cal loaded" if self.calibration_map else ""
        self.midi_status_var.set(f"{len(events)} notes{cal_info}")
        self.midi_play_btn.config(state=tk.NORMAL)

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
        from claxon_core import fire as fire_sync
        events = self.midi_data
        start_time = time.time()
        has_cal = len(self.calibration_map) == len(events)
        cal_map = list(self.calibration_map) if has_cal else []

        for i, (event_time, claxon_idx, dur_ms) in enumerate(events):
            if self.midi_stop_event.is_set():
                if not has_cal:
                    cal_map.extend([-1] * (len(events) - i))
                break

            panel = self.panels[claxon_idx]

            compensation_ms = 0
            if has_cal and cal_map[i] >= 0:
                compensation_ms = cal_map[i]

            fire_time = event_time - (compensation_ms / 1000.0)
            target = start_time + fire_time
            now = time.time()
            if target > now:
                wait = target - now
                if self.midi_stop_event.wait(timeout=wait):
                    if not has_cal:
                        cal_map.extend([-1] * (len(events) - i))
                    break

            esp = self.system.get_esp_for_claxon(claxon_idx)
            if not esp:
                if not has_cal:
                    cal_map.append(-1)
                continue

            channel = self.system.get_channel_for_claxon(claxon_idx)
            result = fire_sync(esp, channel, dur_ms)
            if result["success"]:
                measured = result["startup_delay"]
                if has_cal:
                    residual = measured - compensation_ms
                    cal_map[i] = compensation_ms + residual // 2
                else:
                    cal_map.append(measured)
            else:
                if not has_cal:
                    cal_map.append(-1)

            self.root.after(0, panel.flash)
            if has_cal:
                new_val = cal_map[i]
                delta = new_val - compensation_ms
                sign = "+" if delta >= 0 else ""
                self.root.after(0, lambda idx=i, c=new_val, s=sign, d=delta: self.midi_status_var.set(
                    f"Playing {idx + 1}/{len(events)} (comp={c}ms, {s}{d}ms)"
                ))
            else:
                delay_str = f"{cal_map[-1]}ms" if cal_map[-1] >= 0 else "fail"
                self.root.after(0, lambda idx=i, d=delay_str: self.midi_status_var.set(
                    f"Playing {idx + 1}/{len(events)} (delay={d})"
                ))

        self.calibration_map = cal_map
        if self.midi_filename:
            self.system.settings[f"cal:{self.midi_filename}"] = cal_map
            save_settings(self.system.settings)
        self.root.after(0, self._midi_playback_done)

    def _midi_playback_done(self):
        self.midi_playing = False
        self.midi_play_btn.config(state=tk.NORMAL)
        self.midi_stop_btn.config(state=tk.DISABLED)
        if self.midi_stop_event.is_set():
            self.midi_status_var.set("Stopped")
        else:
            mapped = sum(1 for d in self.calibration_map if d >= 0)
            self.midi_status_var.set(f"Done ({mapped} notes calibrated)")

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.midi_stop_event.set()
            self.system.stop_discovery()


if __name__ == "__main__":
    ClaxonApp().run()
