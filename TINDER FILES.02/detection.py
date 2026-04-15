import cv2
import numpy as np
from ultralytics import YOLO

_model = None


def init():
    global _model
    # Downloads yolov8n.pt (~6 MB) on first run, cached in ~/.cache/ultralytics after
    _model = YOLO("yolov8n.pt")


def person_detected(image_bytes):
    """
    Returns True if at least one person is found in the image.
    Uses YOLOv8n — handles partial bodies, crowds, varied poses and lighting.
    Class 0 = person in COCO. Confidence threshold 0.35 to avoid false positives.
    """
    arr    = np.frombuffer(image_bytes, np.uint8)
    frame  = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    results = _model(frame, classes=[0], conf=0.35, verbose=False)
    return any(len(r.boxes) > 0 for r in results)
