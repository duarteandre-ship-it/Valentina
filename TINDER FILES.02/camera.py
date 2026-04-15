import cv2

_cap = None


def init(index=0):
    global _cap
    _cap = cv2.VideoCapture(index)
    _cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    _cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not _cap.isOpened():
        raise RuntimeError(f"Could not open camera at index {index}")


def capture_frame():
    """Capture one frame and return it as JPEG bytes, or None on failure."""
    if _cap is None:
        return None
    ret, frame = _cap.read()
    if not ret:
        return None
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


def release():
    global _cap
    if _cap:
        _cap.release()
        _cap = None
