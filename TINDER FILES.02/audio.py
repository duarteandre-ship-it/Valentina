"""
Audio playback via pygame mixer (3.5mm jack on Pi / speakers on laptop).
Supports: TTS bytes, pre-recorded files, looping files.
"""
import os
import tempfile
import threading
import pygame

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import config

_lock        = threading.Lock()
_thread      = None
_loop_stop   = False
_loop_thread = None


def init():
    pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)


# ── Internal playback ──────────────────────────────────────────────────────────

def _play_bytes_blocking(mp3_bytes):
    """Write mp3 bytes to temp file, play, delete. Holds _lock."""
    with _lock:
        tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        try:
            tmp.write(mp3_bytes)
            tmp.close()
            pygame.mixer.music.load(tmp.name)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        finally:
            pygame.mixer.music.unload()
            os.unlink(tmp.name)


def _play_file_blocking(filepath):
    """Play a file path directly, blocking. Holds _lock."""
    with _lock:
        pygame.mixer.music.load(filepath)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.unload()


# ── Public API ─────────────────────────────────────────────────────────────────

def play(mp3_bytes):
    """Play TTS bytes in background thread (non-blocking)."""
    global _thread
    stop()
    _thread = threading.Thread(target=_play_bytes_blocking, args=(mp3_bytes,), daemon=True)
    _thread.start()


def play_blocking(mp3_bytes):
    """Play TTS bytes and block until finished."""
    stop()
    _play_bytes_blocking(mp3_bytes)


def play_file(name):
    """Play a pre-recorded file by key name (from config.AUDIO_FILES), non-blocking."""
    global _thread
    stop()
    path = _resolve(name)
    if path is None:
        return
    _thread = threading.Thread(target=_play_file_blocking, args=(path,), daemon=True)
    _thread.start()


def play_file_blocking(name):
    """Play a pre-recorded file by key name, blocking."""
    stop()
    path = _resolve(name)
    if path is None:
        return
    _play_file_blocking(path)


def play_file_looping(name):
    """Loop a pre-recorded file until stop_loop() or stop() is called."""
    global _loop_stop, _loop_thread
    stop_loop()
    path = _resolve(name)
    if path is None:
        return
    _loop_stop = False

    def _loop():
        while not _loop_stop:
            _play_file_blocking(path)

    _loop_thread = threading.Thread(target=_loop, daemon=True)
    _loop_thread.start()


def stop_loop():
    """Stop the looping file."""
    global _loop_stop
    _loop_stop = True
    stop()


def stop():
    """Stop all playback immediately."""
    pygame.mixer.music.stop()


def is_playing():
    return pygame.mixer.music.get_busy()


# ── Helper ─────────────────────────────────────────────────────────────────────

def _resolve(name):
    """Resolve an audio file key to its full path. Returns None if missing."""
    filename = config.AUDIO_FILES.get(name)
    if filename is None:
        print(f"[audio] unknown file key: {name}")
        return None
    path = os.path.join(config.AUDIO_DIR, filename)
    if not os.path.exists(path):
        print(f"[audio] file not found (will skip): {path}")
        return None
    return path
