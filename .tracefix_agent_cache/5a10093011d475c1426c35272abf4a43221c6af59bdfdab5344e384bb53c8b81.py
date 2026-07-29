# tracefix-handler-template: smartroom-v8
import json
import os
import re
from urllib.request import urlopen
from urllib.error import URLError, HTTPError


def parse_date(question):
    if not question:
        return None
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12"
    }
    match = re.search(r'([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})', question)
    if match:
        month_name = match.group(1).lower()
        day = match.group(2).zfill(2)
        year = match.group(3)
        if month_name in months:
            return f"{year}-{months[month_name]}-{day}"
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', question)
    if match:
        return match.group(0)
    return None


def find_recordings(obj):
    recs = []
    if isinstance(obj, dict):
        day = obj.get("day")
        rec = obj.get("rec")
        if isinstance(day, str) and isinstance(rec, str) and day and rec:
            recs.append({"day": day, "rec": rec, "record": obj})
        for v in obj.values():
            recs.extend(find_recordings(v))
    elif isinstance(obj, list):
        for item in obj:
            recs.extend(find_recordings(item))
    return recs


def main():
    request = json.load(open(os.environ["TRACEFIX_FRAME_PATH"], encoding="utf-8"))
    
    source_url = request.get("sourceUrl", "")
    source_mode = request.get("sourceMode", "")
    question = request.get("question", "")
    raw_data_json = request.get("rawDataJson")
    recording_override = request.get("recordingOverride", {})
    
    agent = "OCCUPANCY_ANALYZER"
    kind = "agent"
    
    recs_data = None
    fetch_error = None
    
    if source_mode == "raw-json":
        if isinstance(raw_data_json, str):
            try:
                recs_data = json.loads(raw_data_json)
            except Exception as e:
                fetch_error = f"Failed to parse rawDataJson: {e}"
        elif isinstance(raw_data_json, dict):
            recs_data = raw_data_json
        else:
            fetch_error = "rawDataJson is not a valid JSON string or object."
    else:
        if source_url:
            rec_url = source_url
            if source_url.endswith("/api/v1"):
                rec_url += "/recordings"
            elif not source_url.endswith("/recordings"):
                rec_url += "/recordings"
            try:
                with urlopen(rec_url) as resp:
                    recs_data = json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                fetch_error = f"HTTP error fetching recordings: {e.code} {e.reason}"
            except URLError as e:
                fetch_error = f"URL error fetching recordings: {e.reason}"
            except json.JSONDecodeError as e:
                fetch_error = f"Failed to parse recordings JSON: {e}"
            except Exception as e:
                fetch_error = f"Error fetching recordings: {e}"
        else:
            fetch_error = "sourceUrl not provided for non-raw-json mode."
            
    if fetch_error or recs_data is None:
        result = {
            "ok": True,
            "agent": agent,
            "kind": kind,
            "answer": {
                "answer": "Unable to determine occupancy due to missing or invalid recordings data.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": [fetch_error or "No recordings data available."],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Could not retrieve recordings list."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    all_recs = find_recordings(recs_data)
    unique_recs = {}
    for r in all_recs:
        key = (r["day"], r["rec"])
        if key not in unique_recs:
            unique_recs[key] = r
    
    chosen_rec = None
    needs_clarification = False
    candidates = []
    
    if isinstance(recording_override, dict) and recording_override:
        day = recording_override.get("day")
        rec = recording_override.get("rec")
        if not day or not rec:
            rid = recording_override.get("recordingId")
            if rid and "/" in rid:
                parts = rid.split("/", 1)
                day, rec = parts[0], parts[1]
        if day and rec:
            chosen_rec = unique_recs.get((day, rec))
            if not chosen_rec:
                fetch_error = f"Override recording {day}/{rec} not found in recordings list."
        else:
            fetch_error = "Invalid recordingOverride: missing day and rec."
    else:
        target_date = parse_date(question)
        if target_date:
            matched = [r for r in unique_recs.values() if target_date in r["day"]]
            if len(matched) == 1:
                chosen_rec = matched[0]
            elif len(matched) > 1:
                needs_clarification = True
                for r in matched:
                    rid = r["day"] + "/" + r["rec"]
                    candidates.append({
                        "recordingId": rid,
                        "label": r["rec"],
                        "dateLabel": r["day"],
                        "timeLabel": r["rec"]
                    })
            else:
                fetch_error = f"No recordings found for date {target_date}."
        else:
            fetch_error = "Could not parse a date from the question to match recordings."

    if needs_clarification:
        result = {
            "ok": True,
            "agent": agent,
            "kind": kind,
            "answer": {
                "needsClarification": True,
                "clarificationPrompt": "Multiple recordings match the requested date. Please specify which recording to analyze.",
                "clarificationCandidates": candidates
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    if not chosen_rec:
        result = {
            "ok": True,
            "agent": agent,
            "kind": kind,
            "answer": {
                "answer": "Unable to determine occupancy.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": [fetch_error or "No exact recording could be resolved."],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Recording resolution failed."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    record = chosen_rec["record"]
    cameras = record.get("cameras", {})
    selected_camera = None
    selected_model = None
    inference_url = None
    inference_error = None
    
    for cam_id, cam_data in cameras.items():
        if not isinstance(cam_data, dict):
            continue
        models = cam_data.get("models", {})
        if not isinstance(models, dict):
            continue
        for model_name, status in models.items():
            if status == "done":
                urls = cam_data.get("urls", {})
                if isinstance(urls, dict):
                    inf_url = urls.get("inference")
                    if inf_url and "{model}" in inf_url:
                        selected_camera = cam_id
                        selected_model = model_name
                        inference_url = inf_url.replace("{model}", model_name)
                        break
        if selected_camera:
            break
            
    if not selected_camera:
        inference_error = "No camera/model with status 'done' and a valid inference URL was found."
    elif not inference_url:
        inference_error = "Inference URL template missing or invalid."
        
    if inference_error:
        result = {
            "ok": True,
            "agent": agent,
            "kind": kind,
            "answer": {
                "answer": "Unable to determine occupancy.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": [inference_error],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Inference data unavailable."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    inf_data = None
    try:
        with urlopen(inference_url) as resp:
            inf_data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        inference_error = f"HTTP error fetching inference: {e.code} {e.reason}"
    except URLError as e:
        inference_error = f"URL error fetching inference: {e.reason}"
    except json.JSONDecodeError as e:
        inference_error = f"Failed to parse inference JSON: {e}"
    except Exception as e:
        inference_error = f"Error fetching inference: {e}"
        
    if inference_error or inf_data is None:
        result = {
            "ok": True,
            "agent": agent,
            "kind": kind,
            "answer": {
                "answer": "Unable to determine occupancy.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": [inference_error or "Inference data is empty."],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["Inference fetch failed."]
            },
            "evidence": [],
            "confidence": 0.0
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    persons = inf_data.get("persons", {})
    if isinstance(persons, dict):
        persons = persons.get("persons", {})
    
    time_counts = {}
    if isinstance(persons, dict):
        for track_id, track_data in persons.items():
            if not isinstance(track_data, dict):
                continue
            windows = track_data.get("windows", [])
            if not isinstance(windows, list):
                continue
            for w in windows:
                if not isinstance(w, dict):
                    continue
                t = w.get("t")
                kept = w.get("kept", True)
                if isinstance(t, (int, float)) and kept:
                    if t not in time_counts:
                        time_counts[t] = set()
                    time_counts[t].add(track_id)
                    
    max_concurrent = 0
    if time_counts:
        max_concurrent = max(len(s) for s in time_counts.values())
        
    answer_text = f"Up to {max_concurrent} people were observed at once during the recording."
    caveats = [
        f"Result based on peak concurrent anonymous tracks from camera {selected_camera} using model {selected_model}.",
        "This is a bounded count of simultaneous anonymous tracks, not a continuous room state or identity-based count."
    ]
    
    result = {
        "ok": True,
        "agent": agent,
        "kind": kind,
        "answer": {
            "answer": answer_text,
            "confidence": 0.9,
            "evidence_refs": [inference_url],
            "caveats": caveats,
            "privacy_scope": "cityos_structured_context_only",
            "limitations": ["Occupancy is derived from anonymous track windows at exact timestamps."]
        },
        "evidence": [{"type": "inference_result", "url": inference_url, "max_concurrent": max_concurrent}],
        "confidence": 0.9
    }
    result.setdefault("ok", True)
    print(json.dumps(result))
if __name__ == "__main__":
    main()
