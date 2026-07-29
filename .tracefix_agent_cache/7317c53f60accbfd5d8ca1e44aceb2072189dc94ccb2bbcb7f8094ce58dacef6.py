# tracefix-handler-template: smartroom-v8
import json
import os
import re
from urllib.request import urlopen


def extract_date(question):
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12"
    }
    match = re.search(r'([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})', question)
    if match:
        month = months.get(match.group(1).lower())
        if month:
            day = match.group(2).zfill(2)
            year = match.group(3)
            return f"{year}-{month}-{day}"
    return None


def extract_date_from_day(day_str):
    match = re.search(r'(\d{4}-\d{2}-\d{2})', str(day_str))
    if match:
        return match.group(1)
    return None


def fetch_json(url):
    try:
        with urlopen(url) as response:
            data = response.read().decode('utf-8')
            return json.loads(data), None
    except Exception as e:
        return None, str(e)


def find_recordings(obj, current_day=None):
    results = []
    if isinstance(obj, dict):
        day = obj.get("day", current_day)
        rec = obj.get("rec")
        if day and rec:
            results.append({"day": day, "rec": rec, "data": obj})
        for k, v in obj.items():
            results.extend(find_recordings(v, day))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_recordings(item, current_day))
    return results


def main():
    request = json.load(open(os.environ["TRACEFIX_FRAME_PATH"], encoding="utf-8"))
    
    source_url = request.get("sourceUrl", "")
    source_mode = request.get("sourceMode", "")
    question = request.get("question", "")
    raw_data_json = request.get("rawDataJson")
    override = request.get("recordingOverride")
    
    recordings_data = None
    error_msg = None
    
    if source_mode == "raw-json":
        if isinstance(raw_data_json, str):
            try:
                recordings_data = json.loads(raw_data_json)
            except Exception as e:
                error_msg = f"Failed to parse rawDataJson: {e}"
        elif isinstance(raw_data_json, dict):
            recordings_data = raw_data_json
        else:
            error_msg = "rawDataJson is not a valid JSON string or object."
    else:
        if not source_url:
            error_msg = "sourceUrl is missing."
        else:
            recordings_url = source_url.rstrip("/") + "/recordings"
            recordings_data, error_msg = fetch_json(recordings_url)
    
    if error_msg or recordings_data is None:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "status": "error",
            "answer": "Failed to retrieve recordings data.",
            "caveats": [error_msg or "Unknown error"],
            "confidence": 0.0,
            "evidence_refs": [],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": []
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    found = find_recordings(recordings_data)
    unique_recordings = {}
    for r in found:
        rid = r["day"] + "/" + r["rec"]
        if rid not in unique_recordings:
            unique_recordings[rid] = r
    recordings = list(unique_recordings.values())
    
    matched = []
    if override and isinstance(override, dict):
        target_day = override.get("day")
        target_rec = override.get("rec")
        if not target_day or not target_rec:
            rid = override.get("recordingId")
            if rid and "/" in rid:
                parts = rid.split("/")
                target_day = parts[0]
                target_rec = parts[1]
        if target_day and target_rec:
            matched = [r for r in recordings if r["day"] == target_day and r["rec"] == target_rec]
    else:
        target_date = extract_date(question)
        if target_date:
            matched = [r for r in recordings if extract_date_from_day(r["day"]) == target_date]
    
    if len(matched) != 1:
        candidates = []
        for r in recordings:
            rid = r["day"] + "/" + r["rec"]
            date_label = extract_date_from_day(r["day"]) or r["day"]
            time_label = r["rec"]
            match = re.search(r'(\d{6})', str(r["rec"]))
            if match:
                t = match.group(1)
                time_label = f"{t[0:2]}:{t[2:4]}:{t[4:6]}"
            candidates.append({
                "recordingId": rid,
                "label": rid,
                "dateLabel": date_label,
                "timeLabel": time_label
            })
        
        if not candidates:
            result = {
                "ok": True,
                "agent": "OCCUPANCY_ANALYZER",
                "kind": "runtime monitor",
                "status": "insufficient_evidence",
                "answer": "No recordings found in the source data.",
                "caveats": ["The recordings collection was empty or could not be parsed."],
                "confidence": 0.0,
                "evidence_refs": [],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": []
            }
        else:
            result = {
                "ok": True,
                "agent": "OCCUPANCY_ANALYZER",
                "kind": "runtime monitor",
                "status": "needs_clarification",
                "answer": {
                    "needsClarification": True,
                    "clarificationPrompt": "Please specify which recording to analyze.",
                    "clarificationCandidates": candidates
                }
            }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    chosen = matched[0]
    chosen_data = chosen["data"]
    cameras = chosen_data.get("cameras", {})
    
    selected_camera = None
    selected_model = None
    inference_url = None
    
    for cam_id, cam_data in cameras.items():
        if not isinstance(cam_data, dict):
            continue
        models = cam_data.get("models", {})
        urls = cam_data.get("urls", {})
        inf_template = urls.get("inference")
        if not isinstance(models, dict) or not inf_template:
            continue
        for model_name, status in models.items():
            if status == "done":
                selected_camera = cam_id
                selected_model = model_name
                inference_url = inf_template.replace("{model}", model_name)
                break
        if inference_url:
            break
    
    if not inference_url:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "status": "insufficient_evidence",
            "answer": "No camera/model with status 'done' found for the selected recording.",
            "caveats": ["No available sensor supports 'room_state' context for this space."],
            "confidence": 0.0,
            "evidence_refs": [],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": []
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    inference_data, inf_err = fetch_json(inference_url)
    if inf_err or inference_data is None:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "status": "error",
            "answer": "Failed to fetch inference data.",
            "caveats": [inf_err or "Unknown error"],
            "confidence": 0.0,
            "evidence_refs": [inference_url],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": []
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    persons = inference_data.get("persons", {})
    if isinstance(persons, dict):
        persons = persons.get("persons", {})
    
    timestamp_counts = {}
    if isinstance(persons, dict):
        for track_id, person in persons.items():
            if not isinstance(person, dict):
                continue
            windows = person.get("windows", [])
            if not isinstance(windows, list):
                continue
            for w in windows:
                if not isinstance(w, dict):
                    continue
                t = w.get("t")
                kept = w.get("kept", True)
                if isinstance(t, (int, float)) and kept:
                    if t not in timestamp_counts:
                        timestamp_counts[t] = set()
                    timestamp_counts[t].add(track_id)
    
    if not timestamp_counts:
        result = {
            "ok": True,
            "agent": "OCCUPANCY_ANALYZER",
            "kind": "runtime monitor",
            "status": "insufficient_evidence",
            "answer": "No valid occupancy windows found in inference data.",
            "caveats": ["Inference data did not contain valid windows with numeric timestamps."],
            "confidence": 0.0,
            "evidence_refs": [inference_url],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": []
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return
    
    peak_count = max(len(ids) for ids in timestamp_counts.values())
    
    answer_text = f"up to {peak_count} people were observed at once during the recording"
    caveats = [
        f"Selected camera: {selected_camera}",
        f"Selected model: {selected_model}",
        "Result is peak concurrent anonymous tracks",
        "Privacy scope: cityos_structured_context_only"
    ]
    
    result = {
        "ok": True,
        "agent": "OCCUPANCY_ANALYZER",
        "kind": "runtime monitor",
        "status": "pass",
        "checks": ["recording_resolved", "inference_fetched", "occupancy_computed"],
        "violations": [],
        "answer": answer_text,
        "confidence": 0.9,
        "evidence_refs": [inference_url],
        "caveats": caveats,
        "privacy_scope": "cityos_structured_context_only",
        "limitations": ["No available sensor supports 'room_state' context for this space."]
    }
    result.setdefault("ok", True)
    print(json.dumps(result))
if __name__ == "__main__":
    main()
