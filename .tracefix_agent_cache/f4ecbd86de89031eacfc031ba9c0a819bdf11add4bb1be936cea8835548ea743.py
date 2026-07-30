# tracefix-handler-template: smartroom-v8
import json
import os
import re
from urllib.request import urlopen


def extract_date(question):
    if not question:
        return None
    match = re.search(r'([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})', question)
    if match:
        month_str, day, year = match.groups()
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12
        }
        month = months.get(month_str.lower())
        if month:
            return f"{year}-{month:02d}-{int(day):02d}"
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', question)
    if match:
        return match.group(0)
    return None


def fetch_json(url):
    try:
        with urlopen(url) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception:
        return None


def find_recordings(obj):
    recordings = []
    if isinstance(obj, dict):
        day = obj.get("day")
        rec = obj.get("rec")
        if isinstance(day, str) and isinstance(rec, str) and day and rec:
            recordings.append(obj)
        for v in obj.values():
            recordings.extend(find_recordings(v))
    elif isinstance(obj, list):
        for v in obj:
            recordings.extend(find_recordings(v))
    return recordings


def main():
    request = json.load(open(os.environ["TRACEFIX_FRAME_PATH"], encoding="utf-8"))
    sourceUrl = request.get("sourceUrl")
    sourceMode = request.get("sourceMode")
    question = request.get("question")
    rawDataJson = request.get("rawDataJson")
    recordingOverride = request.get("recordingOverride")

    if sourceMode == "raw-json":
        if isinstance(rawDataJson, str):
            try:
                recordings_data = json.loads(rawDataJson)
            except Exception:
                recordings_data = {}
        else:
            recordings_data = rawDataJson
    else:
        if sourceUrl.endswith("/api/v1"):
            recordings_url = sourceUrl + "/recordings"
        else:
            recordings_url = sourceUrl
        recordings_data = fetch_json(recordings_url)

    if recordings_data is None:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "answer": "Failed to fetch recordings data.",
            "evidence": [],
            "confidence": 0.0,
            "status": "failed",
            "checks": [],
            "violations": ["recordings_fetch_failed"]
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    recordings = find_recordings(recordings_data)
    exact_recording = None

    if recordingOverride and isinstance(recordingOverride, dict):
        day = recordingOverride.get("day")
        rec = recordingOverride.get("rec")
        if not day or not rec:
            rec_id = recordingOverride.get("recordingId", "")
            if "/" in rec_id:
                day, rec = rec_id.split("/", 1)
        if day and rec:
            for r in recordings:
                if r.get("day") == day and r.get("rec") == rec:
                    exact_recording = r
                    break
    else:
        date = extract_date(question)
        if date:
            matches = [r for r in recordings if date in r.get("day", "")]
            if len(matches) == 1:
                exact_recording = matches[0]
            elif len(matches) > 1:
                candidates = []
                for r in matches:
                    day = r.get("day")
                    rec = r.get("rec")
                    candidates.append({
                        "recordingId": f"{day}/{rec}",
                        "label": f"{day} {rec}",
                        "dateLabel": day,
                        "timeLabel": rec
                    })
                result = {
                    "ok": True,
                    "agent": "OCCUPANCY_ANALYZER",
                    "kind": "runtime monitor",
                    "answer": {
                        "needsClarification": True,
                        "clarificationPrompt": "Multiple recordings found for the requested date. Please specify which recording to analyze.",
                        "clarificationCandidates": candidates
                    },
                    "status": "needs_clarification",
                    "checks": [],
                    "violations": []
                }
                result.setdefault("ok", True)
                print(json.dumps(result))
                return

    if not exact_recording:
        candidates = []
        for r in recordings:
            day = r.get("day")
            rec = r.get("rec")
            if day and rec:
                candidates.append({
                    "recordingId": f"{day}/{rec}",
                    "label": f"{day} {rec}",
                    "dateLabel": day,
                    "timeLabel": rec
                })
        if candidates:
            result = {
                "ok": True,
                "agent": "OCCUPANCY_ANALYZER",
                "kind": "runtime monitor",
                "answer": {
                    "needsClarification": True,
                    "clarificationPrompt": "Could not resolve an exact recording. Please specify one.",
                    "clarificationCandidates": candidates
                },
                "status": "needs_clarification",
                "checks": [],
                "violations": []
            }
        else:
            result = {
                "ok": True,
                "agent": "OCCUPANCY_ANALYZER",
                "kind": "runtime monitor",
                "answer": "No recordings found.",
                "evidence": [],
                "confidence": 0.0,
                "status": "failed",
                "checks": [],
                "violations": ["no_recordings"]
            }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    cameras = exact_recording.get("cameras", {})
    selected_camera = None
    selected_model = None
    inference_url = None
    for cam_id, cam_data in cameras.items():
        models = cam_data.get("models", {})
        for model_name, status in models.items():
            if status == "done":
                urls = cam_data.get("urls", {})
                url_template = urls.get("inference")
                if url_template and "{model}" in url_template:
                    selected_camera = cam_id
                    selected_model = model_name
                    inference_url = url_template.replace("{model}", model_name)
                    break
        if inference_url:
            break

    if not inference_url:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "answer": "No camera/model with status 'done' found for the selected recording.",
            "evidence": [],
            "confidence": 0.0,
            "status": "failed",
            "checks": [],
            "violations": ["no_done_model"]
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    inference_data = fetch_json(inference_url)
    if inference_data is None:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "answer": "Failed to fetch inference data.",
            "evidence": [],
            "confidence": 0.0,
            "status": "failed",
            "checks": [],
            "violations": ["inference_fetch_failed"]
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    persons = inference_data.get("persons", {})
    persons_dict = persons.get("persons", {})
    timestamp_counts = {}
    for track_id, track_data in persons_dict.items():
        windows = track_data.get("windows", [])
        for window in windows:
            t = window.get("t")
            kept = window.get("kept", True)
            if isinstance(t, (int, float)) and kept:
                if t not in timestamp_counts:
                    timestamp_counts[t] = set()
                timestamp_counts[t].add(track_id)

    max_concurrent = 0
    if timestamp_counts:
        max_concurrent = max(len(ids) for ids in timestamp_counts.values())

    if not persons_dict:
        answer = "No occupancy data available for the selected recording."
        caveats = ["No persons data found in inference output."]
        confidence = 0.0
    else:
        answer = f"Up to {max_concurrent} people were observed at once during the recording."
        caveats = [f"Result is peak concurrent anonymous tracks from camera {selected_camera}, model {selected_model}."]
        if not timestamp_counts:
            caveats.append("No valid windows with numeric timestamps found.")
        confidence = 1.0 if max_concurrent > 0 else 0.5

    result = {
        "ok": True,
        "agent": "OCCUPANCY_ANALYZER",
        "kind": "runtime monitor",
        "answer": answer,
        "evidence": [
            {
                "recordingId": f"{exact_recording.get('day')}/{exact_recording.get('rec')}",
                "camera": selected_camera,
                "model": selected_model,
                "maxConcurrent": max_concurrent
            }
        ],
        "confidence": confidence,
        "caveats": caveats,
        "privacy_scope": "cityos_structured_context_only",
        "limitations": ["Anonymous track counts only, not identity."],
        "status": "completed",
        "checks": ["validate_agent_state_transitions", "validate_protocol_completion"],
        "violations": []
    }
    result.setdefault("ok", True)
    print(json.dumps(result))
if __name__ == "__main__":
    main()
