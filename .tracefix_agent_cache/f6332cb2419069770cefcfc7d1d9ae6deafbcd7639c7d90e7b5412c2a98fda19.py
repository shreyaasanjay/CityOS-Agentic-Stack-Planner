# tracefix-handler-template: smartroom-v8
import json
import os
import re
import datetime
from urllib.request import urlopen


def extract_date(question):
    if not question:
        return None
    match = re.search(r'([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})', question)
    if match:
        month_str, day_str, year_str = match.groups()
        try:
            dt = datetime.datetime.strptime(f"{month_str} {day_str} {year_str}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            try:
                dt = datetime.datetime.strptime(f"{month_str} {day_str} {year_str}", "%b %d %Y")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    return None


def find_recordings(obj, current_day=None):
    recordings = []
    if isinstance(obj, dict):
        day = obj.get("day", current_day)
        rec = obj.get("rec")
        if day and rec:
            recordings.append({"day": day, "rec": rec, "record": obj})
        for k, v in obj.items():
            if k != "day":
                recordings.extend(find_recordings(v, current_day=day))
    elif isinstance(obj, list):
        for item in obj:
            recordings.extend(find_recordings(item, current_day=current_day))
    return recordings


def main():
    request = json.load(open(os.environ["TRACEFIX_FRAME_PATH"], encoding="utf-8"))
    
    source_url = request.get("sourceUrl", "")
    source_mode = request.get("sourceMode", "")
    question = request.get("question", "")
    raw_data_json = request.get("rawDataJson", "{}")
    override = request.get("recordingOverride")
    
    recordings_data = None
    fetch_error = None
    
    if source_mode == "raw-json":
        try:
            recordings_data = json.loads(raw_data_json)
        except Exception as e:
            fetch_error = f"Failed to parse rawDataJson: {str(e)}"
    else:
        if source_url.endswith("/api/v1"):
            rec_url = source_url + "/recordings"
        else:
            rec_url = source_url.rstrip("/") + "/api/v1/recordings"
        try:
            with urlopen(rec_url) as resp:
                recordings_data = json.load(resp)
        except Exception as e:
            fetch_error = f"Failed to fetch recordings from {rec_url}: {str(e)}"
    
    if fetch_error:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "agent",
            "answer": {
                "answer": "Insufficient evidence to determine occupancy.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": [fetch_error],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Could not retrieve recording data."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    recordings = find_recordings(recordings_data)
    
    chosen = None
    if override and isinstance(override, dict):
        req_day = override.get("day")
        req_rec = override.get("rec")
        if not req_day or not req_rec:
            rid = override.get("recordingId")
            if rid and "/" in rid:
                req_day, req_rec = rid.split("/", 1)
        if req_day and req_rec:
            for r in recordings:
                if r["day"] == req_day and r["rec"] == req_rec:
                    chosen = r
                    break
    else:
        date_str = extract_date(question)
        candidates = []
        if date_str:
            for r in recordings:
                if date_str in r["day"]:
                    candidates.append(r)
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1:
            clarification_candidates = []
            for r in candidates:
                rid = f"{r['day']}/{r['rec']}"
                clarification_candidates.append({
                    "recordingId": rid,
                    "label": f"{r['day']} / {r['rec']}",
                    "dateLabel": r['day'],
                    "timeLabel": r['rec']
                })
            result = {
                "ok": True,
                "agent": "OCCUPANCY_ANALYZER",
                "kind": "agent",
                "answer": {
                    "needsClarification": True,
                    "clarificationPrompt": "Multiple recordings found for the requested date. Please specify which recording to analyze.",
                    "clarificationCandidates": clarification_candidates
                },
                "evidence": [],
                "confidence": 0.0
            }
            result.setdefault("ok", True)
            print(json.dumps(result))
            return
        else:
            result = {
                "ok": True,
                "agent": "OCCUPANCY_ANALYZER",
                "kind": "agent",
                "answer": {
                    "needsClarification": True,
                    "clarificationPrompt": "No recordings found for the requested date. Please specify a valid recording.",
                    "clarificationCandidates": []
                },
                "evidence": [],
                "confidence": 0.0
            }
            result.setdefault("ok", True)
            print(json.dumps(result))
            return
    
    if not chosen:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "agent",
            "answer": {
                "needsClarification": True,
                "clarificationPrompt": "Could not find the specified recording. Please specify a valid recording.",
                "clarificationCandidates": []
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    record = chosen["record"]
    cameras = record.get("cameras", {})
    selected_camera = None
    selected_model = None
    inference_url = None
    
    for cam_id, cam_data in cameras.items():
        if not isinstance(cam_data, dict):
            continue
        models = cam_data.get("models", {})
        urls = cam_data.get("urls", {})
        inf_template = urls.get("inference")
        for model_name, status in models.items():
            if status == "done" and inf_template and "{model}" in inf_template:
                selected_camera = cam_id
                selected_model = model_name
                inference_url = inf_template.replace("{model}", model_name)
                break
        if selected_camera:
            break
    
    if not inference_url:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "agent",
            "answer": {
                "answer": "Insufficient evidence to determine occupancy.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": ["No camera with a completed model was found for the selected recording."],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Could not retrieve inference data."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    inf_data = None
    inf_error = None
    try:
        with urlopen(inference_url) as resp:
            inf_data = json.load(resp)
    except Exception as e:
        inf_error = f"Failed to fetch or parse inference data from {inference_url}: {str(e)}"
    
    if inf_error:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "agent",
            "answer": {
                "answer": "Insufficient evidence to determine occupancy.",
                "confidence": 0.0,
                "evidence_refs": [inference_url],
                "caveats": [inf_error],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Could not retrieve inference data."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    persons = inf_data.get("persons", {}).get("persons", {})
    timestamp_counts = {}
    for track_id, track_data in persons.items():
        if not isinstance(track_data, dict):
            continue
        windows = track_data.get("windows", [])
        for w in windows:
            if not isinstance(w, dict):
                continue
            t = w.get("t")
            kept = w.get("kept", True)
            if isinstance(t, (int, float)) and kept:
                if t not in timestamp_counts:
                    timestamp_counts[t] = set()
                timestamp_counts[t].add(track_id)
    
    max_concurrent = 0
    if timestamp_counts:
        max_concurrent = max(len(ids) for ids in timestamp_counts.values())
    
    answer_text = f"up to {max_concurrent} people were observed at once during the recording."
    caveats = [
        f"Result is peak concurrent anonymous tracks from selected camera {selected_camera} and model {selected_model}.",
        "Cumulative track IDs were not summed."
    ]
    
    result = {
        "ok": True,
        "agent": "OCCUPANCY_ANALYZER",
        "kind": "agent",
        "answer": {
            "answer": answer_text,
            "confidence": 0.9,
            "evidence_refs": [inference_url],
            "caveats": caveats,
            "privacy_scope": "cityos_structured_context_only",
            "limitations": ["Does not sum people across cameras.", "Cumulative track IDs are not occupancy."]
        },
        "evidence": [inf_data],
        "confidence": 0.9
    }
    result.setdefault("ok", True)
    print(json.dumps(result))
if __name__ == "__main__":
    main()
