"""Webcam watcher for Get Your Ass Up. Run it next to the coach:

    uv run --project watcher watcher/watch.py

Every few seconds it sends a frame to the coach, which asks a vision model what you're doing
(desk, phone, exercising, away). Every frame it also runs MediaPipe pose locally to count your
squats, pushups and lunges for the workout you owe. A small preview window shows what it sees;
press q in it to quit.

macOS asks for camera permission the first time, for the terminal app you run this from
(System Settings → Privacy & Security → Camera).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

from messages import camera_problem, link_problem
from reps import RepCounter

COACH = f"http://127.0.0.1:{os.environ.get('GYAU_PORT', '7777')}"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
MODEL_PATH = Path.home() / ".get-your-ass-up" / "models" / "pose_landmarker_lite.task"
CONNECTIONS = [(c.start, c.end) for c in vision.PoseLandmarksConnections.POSE_LANDMARKS]

COLORS = {  # BGR
    "desk": (200, 200, 200),
    "phone": (60, 60, 230),
    "exercising": (80, 200, 80),
    "away": (150, 150, 150),
    None: (120, 120, 120),
}


def post(path: str, *, data: bytes | None = None, body: dict | None = None, timeout: float = 12) -> dict:
    if body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    else:
        headers = {"Content-Type": "image/jpeg"}
    request = urllib.request.Request(COACH + path, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def get_status() -> dict:
    with urllib.request.urlopen(COACH + "/status", timeout=1) as response:
        return json.loads(response.read())


class Link:
    """Talks to the coach from a background thread, so the preview never stutters."""

    def __init__(self, every: float, cloud: bool):
        self.every, self.cloud = every, cloud
        self.lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.label: str | None = None
        self.status: dict = {}
        self.online = False
        self.local_label: str | None = None
        self._reps: list[tuple[str, int]] = []
        self._last_error = ""

    def offer(self, jpeg: bytes, local_label: str) -> None:
        with self.lock:
            self.jpeg, self.local_label = jpeg, local_label

    def counted(self, exercise: str, count: int) -> None:
        with self.lock:
            self._reps.append((exercise, count))

    def run(self) -> None:
        next_frame = 0.0
        while True:
            try:
                with self.lock:
                    reps, self._reps = self._reps, []
                    jpeg, local_label = self.jpeg, self.local_label
                for exercise, count in reps[-1:]:  # only the latest count matters
                    self._update(post("/reps-seen", body={"exercise": exercise, "count": count}))
                if time.monotonic() >= next_frame and jpeg:
                    next_frame = time.monotonic() + self.every
                    if self.cloud:
                        result = post("/frame", data=jpeg)
                        self._update(result, label=result.get("label"))
                    else:
                        self._update(post("/seen", body={"label": local_label}), label=local_label)
                else:
                    self._update(get_status())
                if not self.online:
                    print("Connected to the coach.", flush=True)
                self.online = True
            except Exception as e:
                problem = link_problem(e, COACH)
                if self.online or self._last_error != problem:
                    print(problem, flush=True)
                    self._last_error = problem
                self.online = False
            time.sleep(0.5)

    def _update(self, status: dict, label: str | None = None) -> None:
        with self.lock:
            if status:
                self.status = status
            if label:
                if label != self.label:
                    print(f"Seen: {label}", flush=True)  # so you can tell it's working without the preview
                self.label = label


BACKEND = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
MAX_CAMERAS = 8


def open_camera(index: int):
    return cv2.VideoCapture(index, BACKEND)


def working_cameras(skip: int | None = None) -> dict[int, tuple[int, int]]:
    """Every camera index that opens and gives a frame, with its resolution."""
    found = {}
    for index in range(MAX_CAMERAS):
        if index == skip:
            continue
        camera = open_camera(index)
        ok, frame = False, None
        for _ in range(10 if camera.isOpened() else 0):  # the first frames can come back empty
            ok, frame = camera.read()
            if ok:
                break
            time.sleep(0.1)
        camera.release()
        if ok:
            found[index] = (frame.shape[1], frame.shape[0])
    return found


def camera_names() -> dict[int, str]:
    """Names by index, if ffmpeg is around to ask macOS (best effort; OpenCV has no way to)."""
    if sys.platform != "darwin" or not shutil.which("ffmpeg"):
        return {}
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True, timeout=10,
    ).stderr  # fmt: skip
    video = out.split("video devices:")[-1].split("audio devices:")[0]
    return {int(i): name for i, name in re.findall(r"\[(\d+)\] (.+)", video) if "Capture screen" not in name}


def list_cameras() -> None:
    cameras, names = working_cameras(), camera_names()
    if not cameras:
        raise SystemExit(camera_problem(0, []))
    for index, (w, h) in cameras.items():
        print(f"  --camera {index}   {w}x{h}   {names.get(index, '')}".rstrip())
    print("Pick the one that can see your whole body for rep counting.")


def ensure_model() -> Path:
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading the pose model to {MODEL_PATH} …")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def draw(frame, landmarks, link: Link, counter: RepCounter | None) -> None:
    h, w = frame.shape[:2]
    if landmarks:
        points = [(int(p.x * w), int(p.y * h)) for p in landmarks]
        for a, b in CONNECTIONS:
            cv2.line(frame, points[a], points[b], (255, 255, 255), 2, cv2.LINE_AA)
        for x, y in points[11:]:
            cv2.circle(frame, (x, y), 4, (0, 220, 255), -1, cv2.LINE_AA)

    with link.lock:
        label, status, online = link.label, dict(link.status), link.online
    font = cv2.FONT_HERSHEY_DUPLEX

    def badge(text: str, y: int, color, scale: float = 1.0) -> None:
        (tw, th), _ = cv2.getTextSize(text, font, scale, 2)
        cv2.rectangle(frame, (16, y - th - 14), (16 + tw + 24, y + 10), color, -1)
        cv2.putText(frame, text, (28, y), font, scale, (255, 255, 255), 2, cv2.LINE_AA)

    if not online:
        badge("COACH OFFLINE", 50, (60, 60, 60))
    elif status.get("resting"):
        badge("RESTING", 50, (180, 120, 40))
    else:
        badge((label or "watching").upper(), 50, COLORS.get(label, COLORS[None]))
    if status.get("current") and not status.get("resting"):
        owed = status.get("owed", 0)
        done = counter.count if counter and counter.countable else None
        text = f"{status['current'].upper()}" if done is None else f"{status['exercise'].upper()}  {done} / {owed}"
        badge(text, h - 30, (30, 30, 30), 1.2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=int(os.environ.get("GYAU_CAMERA", "0")))
    parser.add_argument("--every", type=float, default=4.0, help="seconds between labelled frames")
    parser.add_argument("--no-cloud", action="store_true", help="label locally from pose only (can't spot a phone)")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--video", help="read a video file instead of the camera (for testing)")
    parser.add_argument("--seconds", type=float, help="stop after this many seconds")
    parser.add_argument("--list-cameras", action="store_true", help="show which cameras open, then quit")
    args = parser.parse_args()

    if args.list_cameras:
        list_cameras()
        return

    camera = cv2.VideoCapture(args.video) if args.video else open_camera(args.camera)
    if not camera.isOpened():
        if args.video:
            raise SystemExit(f"Couldn't read {args.video}.")
        raise SystemExit(camera_problem(args.camera, sorted(working_cameras(skip=args.camera))))

    landmarker = vision.PoseLandmarker.create_from_options(
        vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=vision.RunningMode.VIDEO,
        )
    )
    link = Link(args.every, cloud=not args.no_cloud)
    threading.Thread(target=link.run, daemon=True).start()

    counter: RepCounter | None = None
    workout_id = None
    started = time.monotonic()
    last_rep = 0.0
    print(f"Watching camera {args.camera}. Coach at {COACH}. Press q in the preview (or ctrl-c) to quit.")
    try:
        while True:
            if args.seconds and time.monotonic() - started > args.seconds:
                break
            ok, frame = camera.read()
            if not ok:
                if args.video:
                    break
                time.sleep(0.05)
                continue
            if args.video:
                time.sleep(1 / 30)  # play files at roughly real time
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int((time.monotonic() - started) * 1000)
            )
            landmarks = result.pose_landmarks[0] if result.pose_landmarks else None

            with link.lock:
                status = dict(link.status)
            # A new workout (or none) means a fresh count.
            if status.get("workout_id") != workout_id:
                workout_id = status.get("workout_id")
                counter = RepCounter(status["exercise"]) if status.get("exercise") else None
            if counter and not status.get("resting") and counter.update(landmarks):
                last_rep = time.monotonic()
                link.counted(counter.exercise, counter.count)

            if landmarks is None:
                local_label = "away"
            elif time.monotonic() - last_rep < 8:
                local_label = "exercising"
            else:
                local_label = "desk"
            small = cv2.resize(frame, (640, int(frame.shape[0] * 640 / frame.shape[1])))
            link.offer(cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes(), local_label)

            if not args.no_window:
                draw(frame, landmarks, link, counter)
                cv2.imshow("Get Your Ass Up: coach cam", cv2.resize(frame, (720, int(frame.shape[0] * 720 / frame.shape[1]))))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
