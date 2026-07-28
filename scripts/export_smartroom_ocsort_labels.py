#!/usr/bin/env python
"""Export smartroom mirror pose detections as OCSort/MOT and YOLO labels.

The smartroom mirror does not expose a literal labels/ directory. It exposes
per-frame COCO-17 keypoints from yolo26n-pose, so this script derives one person
box per detected person and writes tracker-friendly local files.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://smartroom-mirror.vercel.app/api/v1"
DEFAULT_OUTPUT_DIR = ".tracefix-ui/ocsort-labels"
PERSON_CLASS_ID = 0


@dataclass(frozen=True)
class Detection:
    t: float
    sample_frame: int
    native_frame: int
    x: float
    y: float
    w: float
    h: float
    score: float


def fetch_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "tracefix-smartroom-label-export/1"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc.reason}") from exc


def recordings_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("recordings"), list):
        return [item for item in payload["recordings"] if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def keypoint_to_pixel(point: Any, width: int, height: int) -> tuple[float, float] | None:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    x = finite_number(point[0])
    y = finite_number(point[1])
    if x is None or y is None:
        return None
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return x * width, y * height
    return x, y


def box_from_person(
    person: dict[str, Any],
    width: int,
    height: int,
    *,
    keypoint_conf: float,
    padding_ratio: float,
) -> tuple[float, float, float, float, float] | None:
    keypoints = person.get("kpts")
    confidences = person.get("conf") or []
    if not isinstance(keypoints, list):
        return None

    selected: list[tuple[float, float, float]] = []
    fallback: list[tuple[float, float, float]] = []
    for idx, point in enumerate(keypoints):
        pixel = keypoint_to_pixel(point, width, height)
        if pixel is None:
            continue
        conf = finite_number(confidences[idx]) if idx < len(confidences) else None
        score = 1.0 if conf is None else conf
        fallback.append((pixel[0], pixel[1], score))
        if score >= keypoint_conf:
            selected.append((pixel[0], pixel[1], score))

    points = selected if len(selected) >= 2 else fallback
    if len(points) < 2:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x1 = max(0.0, min(xs))
    y1 = max(0.0, min(ys))
    x2 = min(float(width - 1), max(xs))
    y2 = min(float(height - 1), max(ys))
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)

    pad_x = max(4.0, box_w * padding_ratio)
    pad_y = max(4.0, box_h * padding_ratio)
    x1 = max(0.0, x1 - pad_x)
    y1 = max(0.0, y1 - pad_y)
    x2 = min(float(width - 1), x2 + pad_x)
    y2 = min(float(height - 1), y2 + pad_y)

    score = sum(point[2] for point in points) / len(points)
    return x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1), max(0.01, min(1.0, score))


def infer_resolution(payload: dict[str, Any]) -> tuple[int, int]:
    resolution = payload.get("resolution")
    if isinstance(resolution, list) and len(resolution) >= 2:
        width = finite_number(resolution[0])
        height = finite_number(resolution[1])
        if width and height:
            return int(width), int(height)

    calibration = payload.get("calibration")
    if isinstance(calibration, dict):
        width = finite_number(calibration.get("width"))
        height = finite_number(calibration.get("height"))
        if width and height:
            return int(width), int(height)

    return 640, 480


def detections_from_pose(
    payload: dict[str, Any],
    *,
    keypoint_conf: float,
    padding_ratio: float,
) -> tuple[list[Detection], dict[str, Any]]:
    width, height = infer_resolution(payload)
    keypoints = payload.get("keypoints") if isinstance(payload.get("keypoints"), dict) else {}
    frames = keypoints.get("frames") if isinstance(keypoints, dict) else []
    native_fps = finite_number(keypoints.get("nativeFps")) or 30.0
    sample_fps = finite_number(keypoints.get("sampleFps")) or 5.0

    detections: list[Detection] = []
    if not isinstance(frames, list):
        return detections, {"width": width, "height": height, "nativeFps": native_fps, "sampleFps": sample_fps}

    for fallback_index, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            continue
        t = finite_number(frame.get("t"))
        if t is None:
            t = (fallback_index - 1) / sample_fps
        sample_frame = int(round(t * sample_fps)) + 1
        native_frame = int(round(t * native_fps)) + 1
        persons = frame.get("persons")
        if not isinstance(persons, list):
            continue
        for person in persons:
            if not isinstance(person, dict):
                continue
            box = box_from_person(
                person,
                width,
                height,
                keypoint_conf=keypoint_conf,
                padding_ratio=padding_ratio,
            )
            if box is None:
                continue
            x, y, box_w, box_h, score = box
            detections.append(
                Detection(
                    t=t,
                    sample_frame=sample_frame,
                    native_frame=native_frame,
                    x=x,
                    y=y,
                    w=box_w,
                    h=box_h,
                    score=score,
                )
            )

    metadata = {
        "width": width,
        "height": height,
        "nativeFps": native_fps,
        "sampleFps": sample_fps,
        "framesAnalyzed": len(frames),
    }
    return detections, metadata


def safe_seq_name(day: str, rec: str, cam: str) -> str:
    raw = f"{day}_{rec}_{cam}"
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_mot_sequence(
    root: Path,
    seq_name: str,
    detections: list[Detection],
    metadata: dict[str, Any],
    *,
    frame_mode: str,
    video_url: str,
) -> int:
    frame_attr = "native_frame" if frame_mode == "native" else "sample_frame"
    frame_rate = metadata["nativeFps"] if frame_mode == "native" else metadata["sampleFps"]
    seq_length = 0
    lines: list[str] = []
    for det in detections:
        frame = getattr(det, frame_attr)
        seq_length = max(seq_length, frame)
        lines.append(
            f"{frame},-1,{det.x:.2f},{det.y:.2f},{det.w:.2f},{det.h:.2f},{det.score:.4f},{PERSON_CLASS_ID},1.0"
        )

    seq_root = root / seq_name
    write_text(seq_root / "det" / "det.txt", "\n".join(lines) + ("\n" if lines else ""))
    write_text(
        seq_root / "seqinfo.ini",
        "\n".join(
            [
                "[Sequence]",
                f"name={seq_name}",
                "imDir=img1",
                f"frameRate={frame_rate:g}",
                f"seqLength={seq_length}",
                f"imWidth={metadata['width']}",
                f"imHeight={metadata['height']}",
                "imExt=.jpg",
                f"videoUrl={video_url}",
                "",
            ]
        ),
    )
    return len(lines)


def write_yolo_labels(root: Path, seq_name: str, detections: list[Detection], metadata: dict[str, Any]) -> int:
    width = float(metadata["width"])
    height = float(metadata["height"])
    labels_by_frame: dict[int, list[str]] = {}
    for det in detections:
        cx = (det.x + det.w / 2.0) / width
        cy = (det.y + det.h / 2.0) / height
        bw = det.w / width
        bh = det.h / height
        labels_by_frame.setdefault(det.sample_frame, []).append(
            f"{PERSON_CLASS_ID} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f} {det.score:.6f}"
        )

    labels_root = root / seq_name / "labels"
    labels_root.mkdir(parents=True, exist_ok=True)
    for frame, lines in labels_by_frame.items():
        write_text(labels_root / f"{frame:06d}.txt", "\n".join(lines) + "\n")
    return sum(len(lines) for lines in labels_by_frame.values())


def camera_names(recording: dict[str, Any]) -> list[str]:
    cameras = recording.get("cameras")
    if isinstance(cameras, dict):
        return sorted(cameras.keys())
    if isinstance(cameras, list):
        return [str(item) for item in cameras]
    return []


def camera_model_done(recording: dict[str, Any], cam: str, model: str) -> bool:
    cameras = recording.get("cameras")
    if not isinstance(cameras, dict):
        return True
    details = cameras.get(cam)
    if not isinstance(details, dict):
        return True
    models = details.get("models")
    if not isinstance(models, dict):
        return True
    return models.get(model) == "done"


def video_url(base_url: str, day: str, rec: str, cam: str, variant: str) -> str:
    return f"{base_url}/recordings/{day}/{rec}/{cam}/video?variant={variant}"


def export_labels(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    output_dir = Path(args.output).resolve()
    recordings_payload = fetch_json(f"{base_url}/recordings")
    all_recordings = recordings_from(recordings_payload)
    recordings = all_recordings
    if args.day:
        recordings = [item for item in recordings if str(item.get("day", "")) in args.day]
    if args.rec:
        recordings = [item for item in recordings if str(item.get("rec", "")) in args.rec]
    if args.limit_recordings:
        recordings = recordings[: args.limit_recordings]

    manifest: dict[str, Any] = {
        "source": base_url,
        "poseModel": args.model,
        "classNames": ["person"],
        "boxSource": "derived_from_yolo26n_pose_keypoints",
        "keypointConfidenceThreshold": args.keypoint_conf,
        "paddingRatio": args.padding_ratio,
        "filters": {
            "day": args.day,
            "rec": args.rec,
            "camera": args.camera,
            "limitRecordings": args.limit_recordings,
        },
        "sequences": [],
    }

    total_detections = 0
    total_sequences = 0
    for recording in recordings:
        day = str(recording.get("day", "")).strip()
        rec = str(recording.get("rec", "")).strip()
        if not day or not rec:
            continue
        for cam in camera_names(recording):
            if args.camera and cam not in args.camera:
                continue
            if not camera_model_done(recording, cam, args.model):
                continue
            inference_url = f"{base_url}/recordings/{day}/{rec}/{cam}/inference/{args.model}"
            try:
                payload = fetch_json(inference_url)
            except RuntimeError as exc:
                print(f"skip {day}/{rec}/{cam}: {exc}", file=sys.stderr)
                continue

            detections, metadata = detections_from_pose(
                payload,
                keypoint_conf=args.keypoint_conf,
                padding_ratio=args.padding_ratio,
            )
            if not detections:
                print(f"skip {day}/{rec}/{cam}: no keypoint detections", file=sys.stderr)
                continue

            seq_name = safe_seq_name(day, rec, cam)
            raw_video_url = video_url(base_url, day, rec, cam, "raw")
            annotated_video_url = video_url(base_url, day, rec, cam, f"annotated.{args.model}")
            mot_sample_count = write_mot_sequence(
                output_dir / "mot_sample_fps",
                seq_name,
                detections,
                metadata,
                frame_mode="sample",
                video_url=raw_video_url,
            )
            mot_native_count = write_mot_sequence(
                output_dir / "mot_native_fps",
                seq_name,
                detections,
                metadata,
                frame_mode="native",
                video_url=raw_video_url,
            )
            yolo_count = write_yolo_labels(output_dir / "yolo", seq_name, detections, metadata)
            total_detections += yolo_count
            total_sequences += 1
            manifest["sequences"].append(
                {
                    "name": seq_name,
                    "day": day,
                    "rec": rec,
                    "camera": cam,
                    "detections": yolo_count,
                    "framesAnalyzed": metadata["framesAnalyzed"],
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "sampleFps": metadata["sampleFps"],
                    "nativeFps": metadata["nativeFps"],
                    "inferenceUrl": inference_url,
                    "rawVideoUrl": raw_video_url,
                    "annotatedVideoUrl": annotated_video_url,
                    "motSampleDet": str((output_dir / "mot_sample_fps" / seq_name / "det" / "det.txt").resolve()),
                    "motNativeDet": str((output_dir / "mot_native_fps" / seq_name / "det" / "det.txt").resolve()),
                    "yoloLabels": str((output_dir / "yolo" / seq_name / "labels").resolve()),
                    "motSampleDetections": mot_sample_count,
                    "motNativeDetections": mot_native_count,
                }
            )

    write_text(output_dir / "classes.txt", "person\n")
    write_text(output_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    write_text(
        output_dir / "README.txt",
        "\n".join(
            [
                "Smartroom OCSort labels export",
                "",
                "The mirror API does not expose a literal labels folder.",
                "These person boxes are derived from yolo26n-pose COCO keypoints.",
                "",
                "Folders:",
                "- mot_sample_fps/: MOT det.txt using sequential sampled frames at sampleFps.",
                "- mot_native_fps/: MOT det.txt using original video frame numbers at nativeFps.",
                "- yolo/: YOLO-style per-sampled-frame label txt files.",
                "",
                "For OCSort, start with mot_sample_fps unless your tracker is reading the raw 30 FPS video directly.",
                "",
            ]
        ),
    )
    return {
        "outputDir": str(output_dir),
        "recordingsSeen": len(all_recordings),
        "recordingsMatched": len(recordings),
        "sequencesExported": total_sequences,
        "detectionsExported": total_detections,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Smartroom mirror API base URL.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Output labels folder.")
    parser.add_argument("--model", default="yolo26n-pose", help="Pose model endpoint to export.")
    parser.add_argument("--day", action="append", default=[], help="Only export this day id, for example day_13_2026-07-15. Repeat for more than one.")
    parser.add_argument("--rec", action="append", default=[], help="Only export this recording id, for example rec_20260715_003. Repeat for more than one.")
    parser.add_argument("--camera", action="append", default=[], help="Only export this camera id, for example cam2-d455. Repeat for more than one.")
    parser.add_argument("--limit-recordings", type=int, default=0, help="Only export the newest N recordings after filters.")
    parser.add_argument("--keypoint-conf", type=float, default=0.2, help="Minimum keypoint confidence for box extent.")
    parser.add_argument("--padding-ratio", type=float, default=0.12, help="Padding added around keypoint extents.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = export_labels(args)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())