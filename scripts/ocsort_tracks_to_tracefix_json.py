#!/usr/bin/env python
"""Convert OCSort MOT tracks into a TraceFix raw JSON evidence packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_tracks(path: Path) -> dict[str, dict[str, Any]]:
    tracks: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            raise ValueError(f"{path}:{line_number}: expected MOT row frame,track_id,x,y,w,h,score,...")
        frame = int(float(parts[0]))
        track_id = str(int(float(parts[1])))
        if track_id == "-1":
            continue
        x = float(parts[2])
        y = float(parts[3])
        w = float(parts[4])
        h = float(parts[5])
        score = float(parts[6])
        track = tracks.setdefault(
            track_id,
            {
                "id": track_id,
                "trackId": track_id,
                "type": "person",
                "name": f"person {track_id}",
                "firstFrame": frame,
                "lastFrame": frame,
                "frameCount": 0,
                "meanScore": 0.0,
                "boxes": [],
            },
        )
        track["firstFrame"] = min(track["firstFrame"], frame)
        track["lastFrame"] = max(track["lastFrame"], frame)
        track["frameCount"] += 1
        track["meanScore"] += score
        track["boxes"].append({"frame": frame, "x": x, "y": y, "w": w, "h": h, "score": score})
    for track in tracks.values():
        if track["frameCount"]:
            track["meanScore"] = round(track["meanScore"] / track["frameCount"], 4)
    return tracks


def build_packet(args: argparse.Namespace) -> dict[str, Any]:
    tracks_path = Path(args.tracks).resolve()
    tracks = parse_tracks(tracks_path)
    kept_tracks = {
        track_id: track
        for track_id, track in tracks.items()
        if int(track.get("frameCount") or 0) >= args.min_track_frames
    }
    filtered_tracks = {
        track_id: track
        for track_id, track in tracks.items()
        if track_id not in kept_tracks
    }
    track_ids = sorted(kept_tracks, key=lambda value: int(value) if value.isdigit() else value)
    entities = [
        {
            "id": track_id,
            "trackId": track_id,
            "type": "person",
            "name": f"person {track_id}",
            "frame_count": kept_tracks[track_id]["frameCount"],
            "first_frame": kept_tracks[track_id]["firstFrame"],
            "last_frame": kept_tracks[track_id]["lastFrame"],
            "mean_score": kept_tracks[track_id]["meanScore"],
        }
        for track_id in track_ids
    ]
    count = len(track_ids)
    noun = "person" if count == 1 else "people"
    scene_id = args.scene_id or tracks_path.stem
    answer_text = f"There {'is' if count == 1 else 'are'} {count} unique tracked {noun} in {scene_id}."
    return {
        "schema": "tracefix.ocsort.tracks.v1",
        "source": "OCSort tracked MOT output",
        "source_file": str(tracks_path),
        "scene_id": scene_id,
        "question": args.question,
        "answer": {
            "question": args.question,
            "text": answer_text,
            "chatAnswer": answer_text,
            "chat_answer": answer_text,
            "answer": answer_text,
            "count": count,
            "target": "people",
            "method": "unique OCSort track IDs",
            "trackIds": track_ids,
            "sourceKind": "ocsort-tracks",
            "sceneId": scene_id,
        },
        "entities": entities,
        "claims": [
            {
                "claim_type": "object_presence",
                "subject": track_id,
                "predicate": "present",
                "polarity": "positive",
                "modality": "asserted",
                "natural_language": f"OCSort track {track_id} is one unique person in {scene_id}.",
            }
            for track_id in track_ids
        ],
        "tracks": [kept_tracks[track_id] for track_id in track_ids],
        "summary": {
            "uniqueTrackIds": track_ids,
            "uniquePeople": count,
            "minimumTrackFrames": args.min_track_frames,
            "filteredTrackIds": sorted(filtered_tracks, key=lambda value: int(value) if value.isdigit() else value),
            "filteredTracks": [
                {
                    "trackId": track_id,
                    "frameCount": filtered_tracks[track_id]["frameCount"],
                    "firstFrame": filtered_tracks[track_id]["firstFrame"],
                    "lastFrame": filtered_tracks[track_id]["lastFrame"],
                    "reason": f"shorter than {args.min_track_frames} frames",
                }
                for track_id in sorted(filtered_tracks, key=lambda value: int(value) if value.isdigit() else value)
            ],
            "framesWithTracks": len({box["frame"] for track in kept_tracks.values() for box in track["boxes"]}),
            "rows": sum(track["frameCount"] for track in kept_tracks.values()),
            "rawUniqueTrackIds": sorted(tracks, key=lambda value: int(value) if value.isdigit() else value),
            "rawUniquePeople": len(tracks),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", required=True, help="Path to OCSort tracked MOT output txt.")
    parser.add_argument("--output", required=True, help="Path to write TraceFix JSON evidence packet.")
    parser.add_argument("--scene-id", default="", help="Human-readable scene/video id.")
    parser.add_argument("--question", default="How many people are in the room?", help="Question this packet answers.")
    parser.add_argument("--min-track-frames", type=int, default=3, help="Only count tracks seen in at least this many frames.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    packet = build_packet(args)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "uniquePeople": packet["summary"]["uniquePeople"], "trackIds": packet["summary"]["uniqueTrackIds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())