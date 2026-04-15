import serial
import threading
import time

_ser      = None
_callback = None
_thread   = None
_running  = False


def init(port, baud=9600, on_event=None):
    """
    Open serial connection to Arduino and start listening thread.
    on_event(event_string) is called for every line received.
    """
    global _ser, _callback, _thread, _running

    _callback = on_event
    _ser      = serial.Serial(port, baud, timeout=1)
    time.sleep(2)   # Arduino resets on serial connect — wait for it

    _running = True
    _thread  = threading.Thread(target=_read_loop, daemon=True)
    _thread.start()


def _read_loop():
    while _running and _ser:
        try:
            raw = _ser.readline()
            if raw:
                event = raw.decode('utf-8', errors='ignore').strip()
                if event and _callback:
                    _callback(event)
        except Exception:
            pass


def send(command):
    """Send a haptic command string to Arduino."""
    if _ser and _ser.is_open:
        try:
            _ser.write(command.encode('utf-8'))
        except Exception:
            pass


def close():
    global _running
    _running = False
    if _ser:
        _ser.close()
