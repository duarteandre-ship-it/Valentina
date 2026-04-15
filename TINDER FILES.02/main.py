"""
Valentina – AI Dating Assistant
Main script for Raspberry Pi.  Run with:  python main.py
"""
import os
import random
import sys
import time
import threading

import config
import camera
import detection
import arduino
import ai_client
import audio

# ── State ──────────────────────────────────────────────────────────────────────
image_buffer     = []
image_count      = 0
pending_verdict  = None
feedback_ready   = False
is_processing    = False
current_mode     = "hookup"
last_verdict_mp3 = None
in_tutorial      = True

_verdict_lock    = threading.Lock()
_power_standby   = threading.Event()   # set when POWER_OFF received; cleared on restart
_detecting       = False               # True while YOLO is running — blocks re-entry

# ── Tutorial synchronisation ───────────────────────────────────────────────────
_tut_event        = threading.Event()
_tut_waiting_for  = None
_tut_received     = None
_tut_skip         = threading.Event()   # BTN2_LONG during tutorial = skip current section
_tut_abort        = threading.Event()   # flip switch during tutorial = bail out entirely

_tut_audio        = {}                  # pre-generated MP3 bytes keyed by line name
_tut_mode_override = None               # "hookup" | "date" if flip changed during tutorial


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
            arduino.send(haptic_cmd)
            next_haptic = now + haptic_interval


# ── AI pipeline ────────────────────────────────────────────────────────────────

def _process_capture():
    global is_processing, pending_verdict, feedback_ready

    try:
        if current_mode == "hookup" and image_count == config.MAX_IMAGES:
            # 5th image: individual read + game plan run in parallel
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

        with _verdict_lock:
            pending_verdict = verdict
            feedback_ready  = True

        arduino.send(config.HAP_NOTIFY)

    except Exception as e:
        print(f"[AI error] {e}")
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
            arduino.send(config.HAP_NOTIFY)
        except Exception:
            pass
    finally:
        is_processing = False


# ── Button actions ─────────────────────────────────────────────────────────────

def do_capture():
    global image_buffer, image_count, pending_verdict, feedback_ready, is_processing, _detecting

    if image_count >= config.MAX_IMAGES:
        arduino.send(config.HAP_ALERT)
        return

    if _detecting or is_processing:
        speak_plain_async(random.choice(config.STILL_THINKING_LINES))
        return

    img = camera.capture_frame()
    if img is None:
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

                if image_count == 4:
                    threading.Thread(target=lambda: speak_plain(config.LAST_PIC_TEXT), daemon=True).start()

                is_processing = True
                _process_capture()   # runs in this thread; sets is_processing=False when done
            else:
                arduino.send(config.HAP_PULSE)
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
            # Filler → individual verdict with its own haptics
            audio.play_blocking(verdict["filler_mp3"])
            last_verdict_mp3 = verdict["verdict_mp3"]
            _play_with_sentiment_haptics(verdict["verdict_mp3"], verdict["sentiment"])

            # Hookup 5th image: connector → game plan with its own haptics
            if verdict.get("followup"):
                time.sleep(0.5)
                followup = verdict["followup"]
                audio.play_blocking(followup["connector_mp3"])
                last_verdict_mp3 = followup["verdict_mp3"]
                _play_with_sentiment_haptics(followup["verdict_mp3"], followup["sentiment"])

            if image_count >= config.MAX_IMAGES:
                image_buffer = []
                image_count  = 0

        threading.Thread(target=_deliver, daemon=True).start()
        return

    if is_processing:
        speak_plain_async(random.choice(config.STILL_THINKING_LINES))
    else:
        speak_plain_async(random.choice(config.NOTHING_READY_LINES))


def do_repeat():
    if last_verdict_mp3:
        audio.play(last_verdict_mp3)


def do_skip():
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
    """Say farewell and enter standby. Flipping the switch back calls do_power_on."""
    print("[valentina] power off — entering standby")
    audio.stop()
    audio.stop_loop()
    speak_plain(config.FAREWELL_TEXT)
    camera.release()
    _power_standby.set()
    print("[valentina] standby — flip switch back ON to restart")


def do_power_on():
    """Restart the entire Python process fresh (switch flipped back ON from standby)."""
    print("[valentina] restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Arduino event dispatch ─────────────────────────────────────────────────────

def on_arduino_event(event):
    global _tut_received, _tut_mode_override

    # ── Power events — handled regardless of tutorial or operation state ───────
    if event == "POWER_ON":
        if _power_standby.is_set():
            do_power_on()
        return   # ignore POWER_ON when already running normally

    if event == "POWER_OFF":
        if in_tutorial:
            # Abort tutorial so the farewell isn't blocked
            _tut_abort.set()
            _tut_skip.set()
            if _tut_waiting_for:
                _tut_received = event
                _tut_event.set()
        threading.Thread(target=do_power_off, daemon=True).start()
        return

    if in_tutorial:
        # Flip switch during tutorial → bail out of tutorial entirely
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

        skip_events = {"BTN2_LONG", "BTN2_DOUBLE"}

        if event in skip_events:
            if _tut_waiting_for and skip_events.intersection(_tut_waiting_for):
                # BTN2_LONG is the intended target (skip demo step)
                _tut_received = event
                _tut_event.set()
            else:
                # BTN2_LONG = skip current section
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

    # ── Normal operation ───────────────────────────────────────────────────────
    if   event == "BTN1_SHORT":                   do_capture()
    elif event == "BTN1_LONG":                    do_submit()
    elif event in ("BTN2_SHORT", "BTN2_SINGLE"):  do_repeat()
    elif event in ("BTN2_LONG",  "BTN2_DOUBLE"):  do_skip()
    elif event == "FLIP_HOOKUP":                  enter_mode("hookup")
    elif event == "FLIP_DATE":                    enter_mode("date")
    elif event == "POWER_OFF":
        threading.Thread(target=do_shutdown, daemon=True).start()


# ── Tutorial helpers ───────────────────────────────────────────────────────────

def _pregenerate_tutorial_audio():
    """
    Generate all tutorial TTS in parallel before the tutorial starts.
    Eliminates the 1-2s gap between every line that on-demand generation causes.
    """
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
    """
    Play pre-generated tutorial audio, blocking the tutorial thread.
    The keyboard thread can interrupt via audio.stop() at any time —
    pygame.mixer.music.stop() is thread-safe and doesn't need our lock.
    Returns True on completion, False if skipped.
    """
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
    """
    Play a pre-recorded file during tutorial, blocking.
    Same interrupt mechanism as _tut_play.
    Returns True on completion, False if skipped.
    """
    if _tut_skip.is_set() or _tut_abort.is_set():
        return False

    audio.play_file_blocking(name)   # blocks; interruptible by audio.stop()

    if _tut_skip.is_set() or _tut_abort.is_set():
        return False
    return True


def _tut_wait_for(events):
    """
    Block tutorial thread until one of the expected events arrives.
    BTN2_LONG when not expected → skip (returns False).
    Abort → returns False.
    """
    global _tut_waiting_for, _tut_received

    if _tut_abort.is_set():
        return False

    if isinstance(events, str):
        events = [events]

    _tut_event.clear()
    _tut_received    = None
    _tut_waiting_for = set(events)

    _tut_event.wait()

    received         = _tut_received
    _tut_received    = None
    _tut_waiting_for = None

    if _tut_abort.is_set():
        return False

    skip_events = {"BTN2_LONG", "BTN2_DOUBLE"}
    if received in skip_events and not skip_events.intersection(events):
        return False

    return True


# ── Interactive Tutorial ───────────────────────────────────────────────────────
#
# TWO skip levels:
#
#   Block A — Intro (jingle + mode activation)
#             BTN2_LONG → skip entire intro, jump to Block B
#
#   Block B — Mode tutorial + ALL practice steps
#             BTN2_LONG at ANY point → exit tutorial, enter normal operation
#             _tut_skip is NEVER cleared between Block B steps so one press
#             propagates all the way out.
#
# Flipping the mode switch at any point aborts the tutorial immediately.

def _tut_exiting():
    """True if tutorial should bail to normal operation right now."""
    return _tut_skip.is_set() or _tut_abort.is_set()


def run_tutorial():
    global in_tutorial, current_mode

    in_tutorial  = True
    current_mode = "hookup"
    _tut_skip.clear()
    _tut_abort.clear()

    # ── Block A: Intro ────────────────────────────────────────────────────────
    # BTN2_LONG here → skip to Block B

    _tut_play("intro_1")
    _tut_play_file("shawty_lil_baddie")
    _tut_play("intro_2")
    _tut_play("hookup_activated")
    _tut_play_file("hookup_mode_wap")
    _tut_play("randomized_intro")

    if _tut_abort.is_set():
        in_tutorial = False
        return

    _tut_skip.clear()   # ← only clear between blocks, never within Block B

    # ── Block B: Mode tutorial + practice ────────────────────────────────────
    # BTN2_LONG anywhere here → _tut_exiting() becomes True → fall through to exit

    # B1 — Tutorial narration
    _tut_play("main_hookup")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B2 — Capture demo (wait for BTN1_SHORT; BTN2_LONG → exit)
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
        arduino.send(config.HAP_NOTIFY)
        time.sleep(0.4)
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B4 — Long press demo (wait for BTN1_LONG; BTN2_LONG → exit)
    _tut_play("post_haptic")
    if _tut_wait_for("BTN1_LONG"):
        _tut_play("listening")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B5 — BTN2 repeat demo (wait for BTN2_SHORT; BTN2_LONG → exit)
    _tut_play("btn2_intro")
    if _tut_wait_for(["BTN2_SHORT", "BTN2_SINGLE"]):
        _tut_play("slay")
    if _tut_exiting():
        in_tutorial = False
        _tut_skip.clear()
        return

    # B6 — Skip demo (BTN2_LONG is the intended trigger; also exits Block B naturally)
    _tut_play("skip_intro")
    audio.play_file_looping("waiting_sound")
    _tut_wait_for(["BTN2_LONG", "BTN2_DOUBLE"])
    audio.stop_loop()
    _tut_skip.clear()

    if _tut_abort.is_set():
        in_tutorial = False
        return

    # Outro — plays only after full natural completion
    _tut_play("outro")
    _tut_play("outro_whisper")

    in_tutorial = False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global current_mode

    print("[valentina] initialising...")
    camera.init(config.CAMERA_INDEX)
    detection.init()
    audio.init()
    ai_client.init()

    arduino.init(config.ARDUINO_PORT, config.BAUD_RATE, on_event=on_arduino_event)
    time.sleep(0.5)

    print("[valentina] pre-generating tutorial audio...")
    _pregenerate_tutorial_audio()
    print("[valentina] ready — starting tutorial")

    run_tutorial()

    # Determine starting mode: flip switch override > default hookup
    start_mode = _tut_mode_override if _tut_mode_override else "hookup"
    print(f"[valentina] entering {start_mode} mode")

    current_mode = start_mode
    _reset_state()

    # If mode was flipped during tutorial, play the full transition
    if _tut_mode_override:
        enter_mode(start_mode)

    print("[valentina] ready — listening for events")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[valentina] stopping")
    finally:
        camera.release()
        arduino.close()


if __name__ == "__main__":
    main()
