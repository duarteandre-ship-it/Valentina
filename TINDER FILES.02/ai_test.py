"""
Valentina – Pipeline Test
Tests AI vision + TTS independently of all hardware.

Run with:  python ai_test.py
Optional:  python ai_test.py path/to/image.jpg
"""
import sys
import os

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import config
import ai_client
import audio

SAMPLE_TEXT = (
    "Oh honey, I see exactly what we're working with here. "
    "Seven out of ten — potential, but needs some work. "
    "Go say hi before someone else does."
)


def separator(label):
    print(f"\n{'─' * 50}")
    print(f"  {label}")
    print('─' * 50)


def test_tts():
    separator("TEST 1 — Text-to-Speech")
    print(f"Text: \"{SAMPLE_TEXT}\"\n")
    try:
        mp3 = ai_client.text_to_speech(SAMPLE_TEXT)
        print(f"✓ TTS returned {len(mp3):,} bytes of MP3")
        print("  Playing audio now...")
        audio.play_blocking(mp3)
        print("✓ Audio played successfully")
        return True
    except Exception as e:
        print(f"✗ TTS failed: {e}")
        return False


def test_vision(image_path=None):
    separator("TEST 2 — Vision + AI Response")

    # Load image
    if image_path:
        print(f"Using image: {image_path}")
        try:
            with open(image_path, 'rb') as f:
                img_bytes = f.read()
        except Exception as e:
            print(f"✗ Could not read image: {e}")
            return False
    else:
        print("No image path given — capturing from webcam (index 0)...")
        try:
            import camera as cam
            cam.init(config.CAMERA_INDEX)
            img_bytes = cam.capture_frame()
            cam.release()
            if img_bytes is None:
                print("✗ Webcam capture failed")
                return False
            print(f"✓ Captured frame ({len(img_bytes):,} bytes)")
        except Exception as e:
            print(f"✗ Camera error: {e}")
            return False

    # Test both modes
    for mode in ('hookup', 'date'):
        print(f"\n  — Mode: {mode.upper()} —")
        try:
            text = ai_client.get_valentina_response([img_bytes], mode)
            print(f"  Valentina says: \"{text}\"")
            print(f"  Word count: {len(text.split())} words")
            mp3 = ai_client.text_to_speech(text)
            print(f"  Playing {mode} response...")
            audio.play_blocking(mp3)
            print(f"  ✓ {mode} mode OK")
        except Exception as e:
            print(f"  ✗ {mode} mode failed: {e}")
            return False

    return True


def main():
    separator("Valentina – Pipeline Test")
    print("Initialising...")

    audio.init()
    ai_client.init()

    image_path = sys.argv[1] if len(sys.argv) > 1 else None

    results = {
        "TTS":    test_tts(),
        "Vision": test_vision(image_path),
    }

    separator("RESULTS")
    all_passed = True
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All tests passed. Pipeline is ready.")
    else:
        print("Some tests failed. Check your API key and internet connection.")
    print()


if __name__ == "__main__":
    main()
