"""
Claxon Core — общий модуль для управления клаксонами.

Содержит: настройки, mDNS discovery, UDP-команды, MIDI note mapping.
Используется из GUI (control_gui.py) и из любого другого приложения.

Пример использования:
    from claxon_core import ClaxonSystem

    system = ClaxonSystem()
    system.start_discovery()
    time.sleep(3)

    # Выстрелить клаксон 0 (esp-1 ch1)
    result = system.fire(0)

    # Получить маппинг MIDI note → клаксон
    mapping = system.note_to_claxon()
    # {0: 0, 1: 1, 2: 2, ...}  note_class → claxon_index

    # Настройки клаксона
    cfg = system.get_claxon_config(0)
    # {"duration": 50, "threshold": 50, "power": 6.0, "note": 0}

    system.stop_discovery()
"""

import json
import os
import socket
import threading
import time
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

UDP_PORT = 5000
RECV_TIMEOUT = 2.0
NUM_ESPS = 4
CHANNELS_PER_ESP = 2
NUM_CLAXONS = NUM_ESPS * CHANNELS_PER_ESP  # 8
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

ALL_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
DEFAULT_NOTES = [0, 1, 2, 3, 4, 5, 6, 7]  # C, C#, D, D#, E, F, F#, G


# --- Settings ---

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


# --- ESP Device ---

class EspDevice:
    def __init__(self, name: str, ip: str, port: int):
        self.name = name
        self.ip = ip
        self.port = port

    def __repr__(self):
        return f"{self.name} ({self.ip}:{self.port})"


# --- mDNS Discovery ---

class ClaxonDiscovery(ServiceListener):
    def __init__(self, callback=None):
        self.devices: dict[str, EspDevice] = {}
        self.callback = callback

    def update_service(self, zc, type_, name):
        pass

    def remove_service(self, zc, type_, name):
        short_name = name.split(".")[0]
        self.devices.pop(short_name, None)
        if self.callback:
            self.callback()

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port
            short_name = name.split(".")[0]
            self.devices[short_name] = EspDevice(short_name, ip, port)
            if self.callback:
                self.callback()


# --- UDP Commands ---

def send_command(esp: EspDevice, command: str, timeout: float = RECV_TIMEOUT) -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(command.encode(), (esp.ip, esp.port))
        data, _ = sock.recvfrom(256)
        return data.decode()
    except socket.timeout:
        return None
    finally:
        sock.close()


def fire_async(esp: EspDevice, channel: int, duration_ms: int, boost_ms: int | None = None):
    """Отправляет команду без ожидания ответа (для MIDI playback)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if boost_ms is None:
            cmd = f"FIRE:{channel}:{duration_ms}"
        else:
            boost_ms = max(0, int(boost_ms))
            cmd = f"PLAY:{channel}:{duration_ms}:{boost_ms}"
        sock.sendto(cmd.encode(), (esp.ip, esp.port))
    finally:
        sock.close()


def set_threshold(esp: EspDevice, channel: int, threshold: int) -> bool:
    reply = send_command(esp, f"THRESH:{channel}:{threshold}")
    return reply is not None and reply.startswith("THRESH:")


def set_power(esp: EspDevice, channel: int, power: float) -> bool:
    reply = send_command(esp, f"POWER:{channel}:{power:.1f}")
    return reply is not None and reply.startswith("POWER:")


def fire(esp: EspDevice, channel: int, duration_ms: int) -> dict:
    cmd = f"FIRE:{channel}:{duration_ms}"
    reply = send_command(esp, cmd)
    if not reply:
        return {"success": False, "error": "no_response"}

    if reply.startswith("OK:"):
        parts = reply.split(":")
        return {
            "success": True,
            "piezo": int(parts[2]),
            "startup_delay": int(parts[3]),
            "actual_sound_ms": int(parts[4]),
        }

    if reply.startswith("FAIL:"):
        parts = reply.split(":")
        return {
            "success": False,
            "error": parts[2] if len(parts) > 2 else "unknown",
            "piezo": int(parts[3]) if len(parts) > 3 else 0,
        }

    return {"success": False, "error": "unknown"}


def ping(esp: EspDevice) -> bool:
    reply = send_command(esp, "PING")
    return reply is not None and reply.startswith("PONG")


# --- Helpers ---

def claxon_key(index: int) -> str:
    """Клаксон index (0-7) → ключ в settings ("esp-1-ch1")."""
    esp_num = (index // CHANNELS_PER_ESP) + 1
    channel = (index % CHANNELS_PER_ESP) + 1
    return f"esp-{esp_num}-ch{channel}"


def claxon_esp_channel(index: int) -> tuple[str, int]:
    """Клаксон index (0-7) → (esp_name, channel 1-2)."""
    esp_num = (index // CHANNELS_PER_ESP) + 1
    channel = (index % CHANNELS_PER_ESP) + 1
    return f"esp-{esp_num}", channel


# --- ClaxonSystem ---

class ClaxonSystem:
    """
    Центральный объект для работы с клаксонами.
    Читает settings.json, управляет discovery, предоставляет API.
    Можно использовать из любого приложения.
    """

    def __init__(self):
        self.settings = load_settings()
        self.zc: Zeroconf | None = None
        self.listener: ClaxonDiscovery | None = None
        self._on_change_callback = None

    def set_on_change(self, callback):
        """Устанавливает callback, вызываемый при обнаружении/потере ESP."""
        self._on_change_callback = callback

    def start_discovery(self):
        self.stop_discovery()
        self.listener = ClaxonDiscovery(callback=self._on_change_callback)
        self.zc = Zeroconf()
        ServiceBrowser(self.zc, "_claxon._udp.local.", self.listener)

    def stop_discovery(self):
        if self.zc:
            self.zc.close()
            self.zc = None
            self.listener = None

    @property
    def devices(self) -> dict[str, EspDevice]:
        if self.listener:
            return self.listener.devices
        return {}

    def get_esp_for_claxon(self, index: int) -> EspDevice | None:
        esp_name, _ = claxon_esp_channel(index)
        return self.devices.get(esp_name)

    def get_channel_for_claxon(self, index: int) -> int:
        _, channel = claxon_esp_channel(index)
        return channel

    def is_online(self, index: int) -> bool:
        return self.get_esp_for_claxon(index) is not None

    # --- Config ---

    def reload_settings(self):
        self.settings = load_settings()

    def get_claxon_config(self, index: int) -> dict:
        """Возвращает настройки клаксона: duration, threshold, power, note."""
        key = claxon_key(index)
        defaults = {
            "duration": 50,
            "threshold": 50,
            "power": 100.0,
            "note": DEFAULT_NOTES[index] if index < len(DEFAULT_NOTES) else 0,
            "startup_delay_ms": -1,
            "play_comp_ms": 0,
        }
        saved = self.settings.get(key, {})
        defaults.update(saved)
        return defaults

    def set_claxon_config(self, index: int, config: dict):
        """Сохраняет настройки клаксона и синхронизирует с ESP если онлайн."""
        key = claxon_key(index)
        self.settings[key] = config
        save_settings(self.settings)

        esp = self.get_esp_for_claxon(index)
        if esp:
            channel = self.get_channel_for_claxon(index)
            if "threshold" in config:
                set_threshold(esp, channel, config["threshold"])
            if "power" in config:
                set_power(esp, channel, config["power"])

    def sync_claxon_to_esp(self, index: int):
        """Отправляет текущие threshold/power на ESP."""
        esp = self.get_esp_for_claxon(index)
        if not esp:
            return
        cfg = self.get_claxon_config(index)
        channel = self.get_channel_for_claxon(index)
        set_threshold(esp, channel, cfg["threshold"])
        set_power(esp, channel, cfg["power"])

    def sync_all_to_esp(self):
        """Синхронизирует все онлайн клаксоны."""
        for i in range(NUM_CLAXONS):
            self.sync_claxon_to_esp(i)

    # --- Note Mapping ---

    def note_to_claxon(self) -> dict[int, int]:
        """Строит маппинг MIDI note_class (0-11) → claxon_index из настроек."""
        mapping = {}
        for i in range(NUM_CLAXONS):
            cfg = self.get_claxon_config(i)
            mapping[cfg["note"]] = i
        return mapping

    def note_names_list(self) -> list[str]:
        """Список названий нот для каждого клаксона по порядку."""
        return [ALL_NOTE_NAMES[self.get_claxon_config(i)["note"]] for i in range(NUM_CLAXONS)]

    # --- Fire ---

    def fire(self, index: int, duration_ms: int | None = None) -> dict:
        """Выстрелить клаксоном с ожиданием ответа."""
        esp = self.get_esp_for_claxon(index)
        if not esp:
            return {"success": False, "error": "offline"}
        channel = self.get_channel_for_claxon(index)
        if duration_ms is None:
            duration_ms = self.get_claxon_config(index)["duration"]
        return fire(esp, channel, duration_ms)

    def fire_async(self, index: int, duration_ms: int | None = None, boost_ms: int | None = None):
        """Выстрелить без ожидания ответа (для MIDI)."""
        esp = self.get_esp_for_claxon(index)
        if not esp:
            return
        channel = self.get_channel_for_claxon(index)
        if duration_ms is None:
            duration_ms = self.get_claxon_config(index)["duration"]
        fire_async(esp, channel, duration_ms, boost_ms=boost_ms)

    def fire_all(self) -> list[dict]:
        """Выстрелить всеми онлайн клаксонами."""
        results = []
        for i in range(NUM_CLAXONS):
            results.append(self.fire(i))
        return results

    # --- MIDI ---

    def parse_midi(self, path: str) -> list[tuple[float, int, int]]:
        """
        Парсит MIDI файл. Возвращает [(time_sec, claxon_index, duration_ms), ...].
        Использует текущий note mapping из settings.
        """
        import mido
        mid = mido.MidiFile(path)
        note_map = self.note_to_claxon()
        tempo = self._get_midi_tempo(mid)

        events = []
        for track in mid.tracks:
            abs_time = 0.0
            note_on_times: dict[int, float] = {}
            for msg in track:
                abs_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
                if msg.type == "note_on" and msg.velocity > 0:
                    note_class = msg.note % 12
                    if note_class in note_map:
                        note_on_times[msg.note] = abs_time
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    note_class = msg.note % 12
                    if msg.note in note_on_times and note_class in note_map:
                        start = note_on_times.pop(msg.note)
                        dur_ms = int((abs_time - start) * 1000)
                        dur_ms = max(20, min(1000, dur_ms))
                        claxon_idx = note_map[note_class]
                        events.append((start, claxon_idx, dur_ms))

        events.sort(key=lambda e: e[0])
        return events

    @staticmethod
    def _get_midi_tempo(mid):
        import mido
        for track in mid.tracks:
            for msg in track:
                if msg.type == "set_tempo":
                    return msg.tempo
        return mido.bpm2tempo(120)
