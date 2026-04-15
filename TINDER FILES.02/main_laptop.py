"""
Valentina – Laptop Test Version
Full flow simulation with keyboard instead of Arduino hardware.

Run with:  python main_laptop.py

Key mapping:
  C       →  BTN1 short press  (capture image)
  V       →  BTN1 long press   (hear verdict)
  R       →  BTN2 short press  (repeat last verdict)
  S       →  BTN2 long press   (skip / stop audio)
  1       →  Flip → Hookup mode
  2       →  Flip → Date mode
  0       →  Power off (farewell + quit)
  Q / ESC →  Force quit
"""
import os
import random
import sys
import time
import threading

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import config
import camera
import detection
import ai_client
import audio
from pynput import keyboard as kb

# ── State ──────────────────────────────────────────────────────────────────────
image_buffer     = []
image_count      = 0
pending_verdict  = None
feedback_ready   = False
is_processing    = False
current_mode     = "hookup"
last_verdict_mp3 = None
in_tutorial      = True
_running         = True

_verdict_lock    = threading.Lock()
_power_standby   = threading.Event()   # set when POWER_OFF received; cleared on restart
_detecting       = False               # True while YOLO is running — blocks re-entry

# ── Tutorial synchronisation ───────────────────────────────────────────────────
_tut_event        = threading.Event()
_tut_waiting_for  = None
_tut_received     = None
_tut_skip         = threading.Event()
_tut_abort        = threading.Event()

_tut_audio        = {}
_tut_mode_override = None


# ── Fake haptics ───────────────────────────────────────────────────────────────

def haptic(cmd):
    labels = {
        config.HAP_NOTIFY: "╔══ HAPTIC: notify  — two short buzzes",
        config.HAP_HOT:    "╔══ HAPTIC: HOT     — quick pulse",
        config.HAP_MEH:    "╔══ HAPTIC: MEH     — medium pulse",
        config.HAP_YUCKY:  "╔══ HAPTIC: YUCKY   — strong burst",
        config.HAP_ALERT:  "╔══ HAPTIC: alert   — three firm pulses",
        config.HAP_PULSE:  "╔══ HAPTIC: pulse   — no person",
    }
    print(labels.get(cmd, f"╔══ HAPTIC: {cmd.strip()}"))


# ── Audio helpers ──────────────────────────────────────────────────────────────

def speak_plain(text):
    mp3 = ai_client.text_to_speech(text)
    audio.play_blocking(mp3)
    return mp3


def speak_plain_async(text):
    threading.Thread(target=speak_plain, args=(text,), daemon=True).start()


def _play_with_sentiment_haptics(verdict_mp3, sentiment):
    est_duration    = len(verdict_mp3) / 3000.0
    haptic_start    = max(0.0, est_duration - config.SENTIMENT_HAPTIC_DURATION)
    haptic_cmd      = config.SENTIMENT_HAPTIC_CMD.get(sentiment, config.HAP_MEH)
    haptic_interval = config.SENTIMENT_HAPTIC_INTERVAL.get(sentiment, 1.0)

    done = threading.Event()

    def _play():
        audio.play_blocking(verdict_mp3)
        done.set()

    threading.Thread(target=_play, daemon=True).start()

    t_start     = time.time()
    next_haptic = t_start + haptic_start

    while not done.wait(timeout=0.03):
        now = time.time()
        if now >= next_haptic:
            haptic(haptic_cmd)
            next_haptic = now + haptic_interval


# ── AI pipeline ────────────────────────────────────────────────────────────────

def _process_capture():
    global is_processing, pending_verdict, feedback_ready

    try:
        if current_mode == "hookup" and image_count == config.MAX_IMAGES:
            ind_result  = [None]
            plan_result = [None]
            exc         = [None]

            def _get_ind():
                try:
                    ind_result[0] = ai_client.get_hookup_individual_verdict(image_buffer[-1])
                except Exception as e:
                    exc[0] = e

            def _get_plan():
                try:
                    plan_result[0] = ai_client.get_hookup_game_plan(image_buffer)
                except Exception as e:
                    exc[0] = e

            t1 = threading.Thread(target=_get_ind,  daemon=True)
            t2 = threading.Thread(target=_get_plan, daemon=True)
            t1.start(); t2.start()
            t1.join();  t2.join()

            if exc[0]:
                raise exc[0]

            connector_mp3 = ai_client.text_to_speech(
                random.choice(config.HOOKUP_CONNECTOR_LINES)
            )
            verdict = ind_result[0]
            verdict["followup"] = {
                "connector_mp3": connector_mp3,
                "sentiment":     plan_result[0]["sentiment"],
                "text":          plan_result[0]["text"],
                "verdict_mp3":   plan_result[0]["verdict_mp3"],
            }

        elif current_mode == "hookup":
            verdict = ai_client.get_hookup_individual_verdict(image_buffer[-1])
        else:
            verdict = ai_client.get_date_verdict(image_buffer)

        print(f"[AI] sentiment={verdict['sentiment']} | \"{verdict['text']}\"")
        if verdict.get("followup"):
            print(f"[AI] game plan: sentiment={verdict['followup']['sentiment']} | \"{verdict['followup']['text']}\"")

        with _verdict_lock:
            pending_verdict = verdict
            feedback_ready  = True

        haptic(config.HAP_NOTIFY)

    except Exception as e:
        print(f"[AI] error: {e}")
        try:
            err_mp3    = ai_client.text_to_speech(config.PROCESSING_ERROR_TEXT)
            filler_mp3 = ai_client.text_to_speech(random.choice(config.FILLER_LINES))
            with _verdict_lock:
                pending_verdict = {
                    "sentiment":   "meh",
                    "text":        config.PROCESSING_ERROR_TEXT,
                    "filler_mp3":  filler_mp3,
                    "verdict_mp3": err_mp3,
                }
                feedback_ready = True
            haptic(config.HAP_NOTIFY)
        except Exception:
            pass
    finally:
        is_processing = False


# ── Button actions ─────────────────────────────────────────────────────────────

def do_capture():
    global image_buffer, image_count, pending_verdict, feedback_ready, is_processing, _detecting

    if image_count >= config.MAX_IMAGES:
        print("[CAPTURE] buffer full")
        haptic(config.HAP_ALERT)
        return

    if _detecting or is_processing:
        print("[CAPTURE] busy — still thinking")
        speak_plain_async(random.choice(config.STILL_THINKING_LINES))
        return

    print("[CAPTURE] snapping frame...")
    img = camera.capture_frame()
    if img is None:
        print("[CAPTURE] camera returned nothing")
        return

    # Click plays instantly — no waiting for detection
    audio.play_file("photo_click")
    _detecting = True

    def _detect_then_process():
        global image_buffer, image_count, pending_verdict, feedback_ready, is_processing, _detecting
        try:
            if detection.person_detected(img):
                with _verdict_lock:
                    pending_verdict = None
                    feedback_ready  = False

                image_buffer.append(img)
                image_count += 1
                print(f"[CAPTURE] person detected — buffer: {image_count}/{config.MAX_IMAGES}")

                if image_count == 4:
                    threading.Thread(target=lambda: speak_plain(config.LAST_PIC_TEXT), daemon=True).start()

                is_processing = True
                _process_capture()   # runs in this thread; sets is_processing=False when done
            else:
                print("[CAPTURE] no person detected")
                haptic(config.HAP_PULSE)
                speak_plain_async(random.choice(config.REJECTION_LINES))
        finally:
            _detecting = False

    threading.Thread(target=_detect_then_process, daemon=True).start()


def do_submit():
    global pending_verdict, feedback_ready, image_buffer, image_count, last_verdict_mp3

    with _verdict_lock:
        if feedback_ready and pending_verdict:
            verdict         = pending_verdict
            pending_verdict = None
            feedback_ready  = False
        else:
            verdict = None

    if verdict:
        def _deliver():
            global image_buffer, image_count, last_verdict_mp3
            audio.play_blocking(verdict["filler_mp3"])
            last_verdict_mp3 = verdict["verdict_mp3"]
            _play_with_sentiment_haptics(verdict["verdict_mp3"], verdict["sentiment"])

            if verdict.get("followup"):
                time.sleep(0.5)
                followup = verdict["followup"]
                audio.play_blocking(followup["connector_mp3"])
                last_verdict_mp3 = followup["verdict_mp3"]
                _play_with_sentiment_haptics(followup["verdict_mp3"], followup["sentiment"])

            if image_count >= config.MAX_IMAGES:
                print("[BUFFER] cleared after final verdict")
                image_buffer = []
                image_count  = 0

        threading.Thread(target=_deliver, daemon=True).start()
        return

    if is_processing:
        print("[SUBMIT] still processing...")
        speak_plain_async(random.choice(config.STILL_THINKING_LINES))
    else:
        print("[SUBMIT] nothing ready")
        speak_plain_async(random.choice(config.NOTHING_READY_LINES))


def do_repeat():
    if last_verdict_mp3:
        print("[AUDIO] replaying last verdict")
        audio.play(last_verdict_mp3)
    else:
        print("[AUDIO] nothing to repeat yet")


def do_skip():
    print("[AUDIO] skipping")
    audio.stop()


# ── Mode management ────────────────────────────────────────────────────────────

def _reset_state():
    global image_buffer, image_count, pending_verdict, feedback_ready
    global is_processing, last_verdict_mp3, _detecting
    image_buffer     = []
    image_count      = 0
    pending_verdict  = None
    feedback_ready   = False
    is_processing    = False
    last_verdict_mp3 = None
    _detecting       = False


def enter_mode(mode):
    global current_mode
    audio.stop()
    _reset_state()
    current_mode = mode
    print(f"\n[MODE] → {mode.upper()}")

    if mode == "hookup":
        announce_text = config.HOOKUP_MODE_ANNOUNCE
        context_text  = config.HOOKUP_MODE_CONTEXT
        sound_file    = "hookup_mode_wap"
    else:
        announce_text = config.DATE_MODE_ANNOUNCE
        context_text  = config.DATE_MODE_CONTEXT
        sound_file    = "date_mode_activated"

    def _go():
        # Generate announce + context in parallel — both ready before soundbite ends
        announce_mp3 = [None]
        context_mp3  = [None]

        def _gen_announce(): announce_mp3[0] = ai_client.text_to_speech(announce_text)
        def _gen_context():  context_mp3[0]  = ai_client.text_to_speech(context_text)

        t1 = threading.Thread(target=_gen_announce, daemon=True)
        t2 = threading.Thread(target=_gen_context,  daemon=True)
        t1.start(); t2.start()
        t1.join()   # wait only for announce before playing

        if announce_mp3[0]:
            audio.play_blocking(announce_mp3[0])
        audio.play_file_blocking(sound_file)

        t2.join()   # context is done well before soundbite ends
        if context_mp3[0]:
            audio.play_blocking(context_mp3[0])

    threading.Thread(target=_go, daemon=True).start()


def do_power_off():
    """Say farewell and enter standby. Key '9' (POWER_ON) restarts."""
    global _running
    print("[valentina] power off — entering standby")
    audio.stop()
    audio.stop_loop()
    speak_plain(config.FAREWELL_TEXT)
    camera.release()
    _power_standby.set()
    print("[valentina] standby — press 9 to restart")


def do_power_on():
    """Restart the entire Python process fresh."""
    print("[valentina] restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Keyboard handler ───────────────────────────────────────────────────────────

def on_key_press(key):
    global _tut_received, _tut_mode_override, _running

    try:
        ch = key.char.lower() if hasattr(key, 'char') and key.char else None
    except AttributeError:
        ch = None

    if ch == 'q' or key == kb.Key.esc:
        _running = False
        audio.stop()
        return False

    keymap = {
        'c': "BTN1_SHORT",
        'v': "BTN1_LONG",
        'r': "BTN2_SHORT",
        's': "BTN2_LONG",
        '1': "FLIP_HOOKUP",
        '2': "FLIP_DATE",
        '0': "POWER_OFF",
        '9': "POWER_ON",
    }
    event = keymap.get(ch)
    if event is None:
        return

    # ── Power events — handled regardless of tutorial or operation state ───────
    if event == "POWER_ON":
        if _power_standby.is_set():
            do_power_on()
        return   # ignore POWER_ON when already running normally

    if event == "POWER_OFF":
        if in_tutorial:
            _tut_abort.set()
            _tut_skip.set()
            if _tut_waiting_for:
                _tut_received = event
                _tut_event.set()
        threading.Thread(target=do_power_off, daemon=True).start()
        return

    if in_tutorial:
        if event in ("FLIP_HOOKUP", "FLIP_DATE"):
            _tut_mode_override = "date" if event == "FLIP_DATE" else "hookup"
            audio.stop()
            audio.stop_loop()
            _tut_abort.set()
            _tut_skip.set()
            if _tut_waiting_for:
                _tut_received = event
                _tut_event.set()
            return

        skip_events = {"BTN2_LONG"}

        if event in skip_events:
            if _tut_waiting_for and skip_events.intersection(_tut_waiting_for):
                _tut_received = event
                _tut_event.set()
            else:
                audio.stop()
                _tut_skip.set()
                if _tut_waiting_for:
                    _tut_received = event
                    _tut_event.set()
            return

        if _tut_waiting_for and event in _tut_waiting_for:
            _tut_received = event
            _tut_event.set()
        return

    # Normal operation
    if   event == "BTN1_SHORT":   do_capture()
    elif event == "BTN1_LONG":    do_submit()
    elif event == "BTN2_SHORT":   do_repeat()
    elif event == "BTN2_LONG":    do_skip()
    elif event == "FLIP_HOOKUP":  enter_mode("hookup")
    elif event == "FLIP_DATE":    enter_mode("date")


# ── Tutorial helpers ───────────────────────────────────────────────────────────

def _pregenerate_tutorial_audio():
    lines = {
        "intro_1":          config.TUT_INTRO_1,
        "intro_2":          config.TUT_INTRO_2,
        "hookup_activated": config.TUT_HOOKUP_ACTIVATED,
        "main_hookup":      config.TUT_MAIN_HOOKUP,
        "good_girl":        config.TUT_GOOD_GIRL,
        "haptic_demo":      config.TUT_HAPTIC_DEMO,
        "post_haptic":      config.TUT_POST_HAPTIC,
        "listening":        config.TUT_LISTENING,
        "btn2_intro":       config.TUT_BTN2_INTRO,
        "slay":             config.TUT_SLAY,
        "skip_intro":       config.TUT_SKIP_INTRO,
        "outro":            config.TUT_OUTRO,
        "outro_whisper":    config.TUT_OUTRO_WHISPER,
        "randomized_intro": random.choice(config.HOOKUP_RANDOMIZED_INTRO),
    }

    results = {}
    lock    = threading.Lock()

    def _gen(key, text):
        try:
            mp3 = ai_client.text_to_speech(text)
            with lock:
                results[key] = mp3
        except Exception as e:
            print(f"[tutorial] TTS pre-gen failed for '{key}': {e}")

    threads = [threading.Thread(target=_gen, args=(k, v), daemon=True) for k, v in lines.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    _tut_audio.update(results)


def _tut_play(key):
    if _tut_skip.is_set() or _tut_abort.is_set():
        return False
    mp3 = _tut_audio.get(key)
    if not mp3:
        return True
    audio.play_blocking(mp3)   # blocks; interruptible by audio.stop() from kb thread
    if _tut_skip.is_set() or _tut_abort.is_set():
        return False
    return True


def _tut_play_file(name):
    if _tut_skip.is_set() or _tut_abort.is_set():
        return False
    audio.play_file_blocking(name)   # blocks; interruptible by audio.stop()
    if _tut_skip.is_set() or _tut_abort.is_set():
        return False
    return True


def _tut_exiting():
    """True if tutorial should bail to normal operation right now."""
    return _tut_skip.is_set() or _tut_abort.is_set()


def _tut_wait_for(events):
    global _tut_waiting_for, _tut_received

    if _tut_abort.is_set():
        return False

    if isinstance(events, str):
        events = [events]

    _tut_event.clear()
    _tut_received    = None
    _tut_waiting_for = set(events)

    print(f"[TUTORIAL] waiting for: {events}")
    _tut_event.wait()

    received         = _tut_received
    _tut_received    = None
    _tut_waiting_for = None
    print(f"[TUTORIAL] received: {received}")

    if _tut_abort.is_set():
        return False

    skip_events = {"BTN2_LONG"}
    if received in skip_events and not skip_events.intersection(events):
        return False

    return True


# ── Interactive Tutorial ───────────────────────────────────────────────────────

def run_tutorial():
    global in_tutorial, current_mode

    in_tutorial  = True
    current_mode = "hookup"
    _tut_skip.clear()
    _tut_abort.clear()

    print("\n── TUTORIAL START ──────────────────────────────────────")
    print("  Block A: S = skip intro  |  Block B: S = skip to normal operation")
    print("  1/2 = flip mode switch (exits tutorial immediately)")
    print("────────────────────────────────────────────────────────\n")

    # ── Block A: Intro ────────────────────────────────────────────────────────
    # One S press → skip entire intro, jump to Block B
    print("[Block A] Intro  —  S to skip to mode tutorial")
    _tut_play("intro_1")
    _tut_play_file("shawty_lil_baddie")
    _tut_play("intro_2")
    _tut_play("hookup_activated")
    _tut_play_file("hookup_mode_wap")
    _tut_play("randomized_intro")

    if _tut_abort.is_set():
        in_tutorial = False
        return
    _tut_skip.clear()   # ← only clear here, never within Block B

    # ── Block B: Mode tutorial + ALL practice steps ───────────────────────────
    # One S press anywhere here → exit straight to normal operation.
    # _tut_skip is NEVER cleared between steps — one press propagates all the way out.
    print("[Block B] Mode tutorial + practice  —  S to exit to normal operation")

    # B1 — Tutorial narration
    _tut_play("main_hookup")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B2 — Capture demo
    print("  → press C to demo camera button")
    if _tut_wait_for("BTN1_SHORT"):
        _tut_play_file("photo_click")
        _tut_play("good_girl")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B3 — Haptic demo
    _tut_play("haptic_demo")
    for _ in range(3):
        if _tut_exiting():
            break
        haptic(config.HAP_NOTIFY)
        time.sleep(0.4)
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B4 — Long press demo
    print("  → press V to demo long press")
    _tut_play("post_haptic")
    if _tut_wait_for("BTN1_LONG"):
        _tut_play("listening")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B5 — BTN2 repeat demo
    print("  → press R to demo repeat button")
    _tut_play("btn2_intro")
    if _tut_wait_for("BTN2_SHORT"):
        _tut_play("slay")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B6 — Skip demo (S IS the intended trigger; also exits Block B naturally)
    print("  → press S to stop the elevator music and finish tutorial")
    _tut_play("skip_intro")
    audio.play_file_looping("waiting_sound")
    _tut_wait_for("BTN2_LONG")
    audio.stop_loop()
    _tut_skip.clear()

    if _tut_abort.is_set():
        in_tutorial = False
        return

    # Outro — only plays after full natural completion of all demos
    _tut_play("outro")
    _tut_play("outro_whisper")

    in_tutorial = False
    print("── TUTORIAL END ────────────────────────────────────────\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def print_controls():
    print("""
┌──────────────────────────────────────────────────────┐
│                 KEYBOARD CONTROLS                    │
├─────────┬────────────────────────────────────────────┤
│  C      │  Capture (BTN1 short)                      │
│  V      │  Hear verdict (BTN1 long)                  │
│  R      │  Repeat last verdict (BTN2 short)          │
│  S      │  Skip section / stop audio (BTN2 long)     │
│  1      │  Hookup mode (flip switch)                 │
│  2      │  Date mode (flip switch)                   │
│  0      │  Power off (standby)                       │
│  9      │  Power on (restart — after standby)        │
│  Q/ESC  │  Force quit                                │
└─────────┴────────────────────────────────────────────┘
""")


def main():
    global current_mode

    print("═" * 54)
    print("  Valentina — Laptop Test Mode")
    print("═" * 54)
    print_controls()

    camera.init(config.CAMERA_INDEX)
    detection.init()
    audio.init()
    ai_client.init()

    listener = kb.Listener(on_press=on_key_press)
    listener.start()

    print("Pre-generating tutorial audio (parallel)...")
    _pregenerate_tutorial_audio()
    print("Ready — starting tutorial\n")

    run_tutorial()

    start_mode = _tut_mode_override if _tut_mode_override else "hookup"
    current_mode = start_mode
    _reset_state()

    if _tut_mode_override:
        enter_mode(start_mode)
    else:
        print(f"[valentina] {start_mode} mode active — ready\n")

    try:
        while _running and listener.is_alive():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        camera.release()
        print("[valentina] goodbye")


if __name__ == "__main__":
    main()
