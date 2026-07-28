#!/usr/bin/env python
"""Run OCSort on a MOT-format det.txt file.

Input rows are MOTChallenge detections:
frame,-1,x,y,w,h,score,class,visibility

Output rows are MOTChallenge tracks:
frame,track_id,x,y,w,h,score,class,visibility
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np


IMPORT_CANDIDATES = [
    ("trackers.ocsort_tracker.ocsort", "OCSort"),
    ("ocsort_tracker.ocsort", "OCSort"),
    ("ocsort.ocsort", "OCSort"),
    ("ocsort", "OCSort"),
]


def load_ocsort(ocsort_root: str | None) -> type[Any]:
    if ocsort_root:
        sys.path.insert(0, str(Path(ocsort_root).resolve()))
    elif os.environ.get("OCSORT_ROOT"):
        sys.path.insert(0, str(Path(os.environ["OCSORT_ROOT"]).resolve()))

    errors: list[str] = []
    for module_name, attr in IMPORT_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
            return getattr(module, attr)
        except Exception as exc:  # noqa: BLE001 - report import attempts to the user.
            errors.append(f"{module_name}.{attr}: {exc}")
    raise RuntimeError(
        "Could not import OCSort. Install OCSort or pass --ocsort-root C:\\path\\to\\OC_SORT.\n"
        + "Tried:\n- "
        + "\n- ".join(errors)
    )


def parse_seqinfo(sequence_dir: Path) -> dict[str, float]:
    info = {"width": 640.0, "height": 480.0, "frame_rate": 5.0}
    seqinfo = sequence_dir / "seqinfo.ini"
    if not seqinfo.exists():
        return info
    for raw_line in seqinfo.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if "=" not in line or line.startswith("["):
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        try:
            numeric = float(value)
        except ValueError:
            continue
        if key == "imWidth":
            info["width"] = numeric
        elif key == "imHeight":
            info["height"] = numeric
        elif key == "frameRate":
            info["frame_rate"] = numeric
    return info


def resolve_det_path(args: argparse.Namespace) -> tuple[Path, Path | None]:
    if args.sequence:
        sequence_dir = Path(args.sequence).resolve()
        return sequence_dir / "det" / "det.txt", sequence_dir
    if not args.det:
        raise ValueError("Pass either --sequence <sequence-folder> or --det <det.txt>.")
    return Path(args.det).resolve(), None


def read_mot_detections(det_path: Path, min_score: float) -> dict[int, list[list[float]]]:
    frames: dict[int, list[list[float]]] = {}
    for line_number, raw_line in enumerate(det_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            raise ValueError(f"{det_path}:{line_number}: expected at least 7 comma-separated columns")
        frame = int(float(parts[0]))
        x = float(parts[2])
        y = float(parts[3])
        w = float(parts[4])
        h = float(parts[5])
        score = float(parts[6])
        if score < min_score:
            continue
        frames.setdefault(frame, []).append([x, y, x + w, y + h, score])
    return frames


def instantiate_tracker(ocsort_class: type[Any], args: argparse.Namespace) -> Any:
    requested = {
        "det_thresh": args.det_thresh,
        "max_age": args.max_age,
        "min_hits": args.min_hits,
        "iou_threshold": args.iou_threshold,
        "delta_t": args.delta_t,
        "asso_func": args.asso_func,
        "inertia": args.inertia,
        "use_byte": args.use_byte,
    }
    signature = inspect.signature(ocsort_class)
    kwargs = {name: value for name, value in requested.items() if name in signature.parameters}
    return ocsort_class(**kwargs)


def update_tracker(tracker: Any, dets: np.ndarray, height: int, width: int) -> np.ndarray:
    try:
        tracks = tracker.update(dets, (height, width), (height, width))
    except TypeError:
        tracks = tracker.update(dets)
    return np.asarray(tracks, dtype=float)


def write_tracks(
    tracker: Any,
    detections_by_frame: dict[int, list[list[float]]],
    output_path: Path,
    *,
    width: int,
    height: int,
) -> int:
    max_frame = max(detections_by_frame.keys(), default=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for frame in range(1, max_frame + 1):
            detections = detections_by_frame.get(frame, [])
            dets = np.asarray(detections, dtype=float).reshape((-1, 5)) if detections else np.empty((0, 5), dtype=float)
            tracks = update_tracker(tracker, dets, height, width)
            if tracks.size == 0:
                continue
            tracks = tracks.reshape((-1, tracks.shape[-1]))
            for track in tracks:
                if len(track) < 5:
                    continue
                x1, y1, x2, y2 = track[:4]
                track_id = int(round(track[4]))
                score = float(track[5]) if len(track) > 5 else 1.0
                x = max(0.0, float(x1))
                y = max(0.0, float(y1))
                w = max(0.0, float(x2) - x)
                h = max(0.0, float(y2) - y)
                handle.write(f"{frame},{track_id},{x:.2f},{y:.2f},{w:.2f},{h:.2f},{score:.4f},0,1.0\n")
                rows_written += 1
    return rows_written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence", help="MOT sequence folder containing det/det.txt and seqinfo.ini.")
    parser.add_argument("--det", help="Path to a MOT det.txt file.")
    parser.add_argument("--output", required=True, help="Where to write tracked MOT rows.")
    parser.add_argument("--ocsort-root", help="Path to a cloned OCSort repo, if OCSort is not installed as a package.")
    parser.add_argument("--img-width", type=int, help="Image width override when using --det directly.")
    parser.add_argument("--img-height", type=int, help="Image height override when using --det directly.")
    parser.add_argument("--min-score", type=float, default=0.0, help="Drop input detections below this confidence.")
    parser.add_argument("--det-thresh", type=float, default=0.3, help="OCSort detection threshold.")
    parser.add_argument("--max-age", type=int, default=30)
    parser.add_argument("--min-hits", type=int, default=3)
    parser.add_argument("--iou-threshold", type=float, default=0.3)
    parser.add_argument("--delta-t", type=int, default=3)
    parser.add_argument("--asso-func", default="iou")
    parser.add_argument("--inertia", type=float, default=0.2)
    parser.add_argument("--use-byte", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    det_path, sequence_dir = resolve_det_path(args)
    if not det_path.exists():
        raise FileNotFoundError(det_path)

    seqinfo = parse_seqinfo(sequence_dir) if sequence_dir else {"width": 640.0, "height": 480.0, "frame_rate": 5.0}
    width = int(args.img_width or seqinfo["width"])
    height = int(args.img_height or seqinfo["height"])

    ocsort_class = load_ocsort(args.ocsort_root)
    tracker = instantiate_tracker(ocsort_class, args)
    detections_by_frame = read_mot_detections(det_path, args.min_score)
    rows_written = write_tracks(
        tracker,
        detections_by_frame,
        Path(args.output).resolve(),
        width=width,
        height=height,
    )
    print(
        json.dumps(
            {
                "detPath": str(det_path),
                "output": str(Path(args.output).resolve()),
                "framesWithDetections": len(detections_by_frame),
                "tracksWritten": rows_written,
                "width": width,
                "height": height,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())