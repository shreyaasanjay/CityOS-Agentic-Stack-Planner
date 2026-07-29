# tracefix-handler-template: smartroom-v8
import json
import os
import re
from urllib.request import urlopen


def parse_date(question):
    match = re.search(r'([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})', question)
    if match:
        month_str, day_str, year_str = match.groups()
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
        }
        month = months.get(month_str.lower())
        if month:
            return f"{year_str}-{month:02d}-{int(day_str):02d}"
    return None


def fetch_json(url):
    try:
        with urlopen(url) as resp:
            data = resp.read().decode('utf-8')
            return json.loads(data), None
    except Exception as e:
        return None, str(e)


def main():
    request = json.load(open(os.environ["TRACEFIX_FRAME_PATH"], encoding="utf-8"))
    source_url = request.get("sourceUrl", "")
    source_mode = request.get("sourceMode", "")
    question = request.get("question", "")
    raw_data_json = request.get("rawDataJson", "")
    recording_override = request.get("recordingOverride", {})

    agent_name = "TRACEFIX_ANSWER"
    result = {
        "agent": agent_name,
        "kind": "agent",
        "answer": None,
        "evidence": [],
        "confidence": 0.0,
        "ok": True
    }

    recordings_data = None
    error_msg = None

    if source_mode == "raw-json":
        try:
            if isinstance(raw_data_json, str):
                recordings_data = json.loads(raw_data_json)
            else:
                recordings_data = raw_data_json
        except Exception as e:
            error_msg = f"Failed to parse rawDataJson: {e}"
    else:
        if source_url:
            rec_url = source_url.rstrip('/') + "/recordings"
            recordings_data, error_msg = fetch_json(rec_url)
        else:
            error_msg = "No sourceUrl provided"

    if error_msg or not recordings_data:
        result["answer"] = {
            "answer": "Insufficient evidence: could not retrieve recordings.",
            "confidence": 0.0,
            "evidence_refs": [],
            "caveats": [f"Error fetching recordings: {error_msg}"],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": ["No recording data available."]
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    recordings = []
    if isinstance(recordings_data, dict):
        recordings = recordings_data.get("recordings", [])
    elif isinstance(recordings_data, list):
        recordings = recordings_data

    target_day = None
    target_rec = None
    if recording_override and isinstance(recording_override, dict):
        target_day = recording_override.get("day")
        target_rec = recording_override.get("rec")
        if (not target_day or not target_rec) and recording_override.get("recordingId"):
            rid = recording_override.get("recordingId")
            parts = rid.split('/')
            if len(parts) == 2:
                target_day, target_rec = parts

    selected_rec = None
    candidates = []

    for rec in recordings:
        if not isinstance(rec, dict):
            continue
        day = rec.get("day")
        rec_id = rec.get("rec")
        if not day or not rec_id:
            continue

        label = f"{day} / {rec_id}"
        candidates.append({
            "recordingId": f"{day}/{rec_id}",
            "label": label,
            "dateLabel": str(day),
            "timeLabel": str(rec_id)
        })

        if target_day and target_rec:
            if str(day) == str(target_day) and str(rec_id) == str(target_rec):
                selected_rec = rec
                break
        else:
            req_date = parse_date(question)
            if req_date and req_date in str(day):
                selected_rec = rec
                break

    if not selected_rec:
        if not candidates:
            result["answer"] = {
                "answer": "Insufficient evidence: no recordings found.",
                "confidence": 0.0,
                "evidence_refs": [],
                "caveats": ["No recordings available in the source."],
                "privacy_scope": "cityos_structured_context_only",
                "limitations": ["No recording data available."]
            }
        else:
            result["answer"] = {
                "needsClarification": True,
                "clarificationPrompt": "Multiple or no exact recordings matched the request. Please specify a recording.",
                "clarificationCandidates": candidates[:5]
            }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    cameras = selected_rec.get("cameras", {})
    selected_cam = None
    selected_model = None
    inference_url = None

    for cam_id, cam_data in cameras.items():
        if not isinstance(cam_data, dict):
            continue
        models = cam_data.get("models", {})
        urls = cam_data.get("urls", {})
        inf_tpl = urls.get("inference")

        for model_name, status in models.items():
            if status == "done" and inf_tpl and "{model}" in inf_tpl:
                selected_cam = cam_id
                selected_model = model_name
                inference_url = inf_tpl.replace("{model}", model_name)
                break
        if selected_cam:
            break

    if not inference_url:
        result["answer"] = {
            "answer": "Insufficient evidence: no completed camera/model inference found for the selected recording.",
            "confidence": 0.0,
            "evidence_refs": [f"{selected_rec.get('day')}/{selected_rec.get('rec')}"],
            "caveats": ["No camera/model with status 'done' and valid inference URL found."],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": ["Inference data unavailable."]
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    inference_data, inf_error = fetch_json(inference_url)
    if inf_error or not inference_data:
        result["answer"] = {
            "answer": "Insufficient evidence: failed to fetch inference data.",
            "confidence": 0.0,
            "evidence_refs": [inference_url],
            "caveats": [f"Error fetching inference: {inf_error}"],
            "privacy_scope": "cityos_structured_context_only",
            "limitations": ["Inference fetch failed."]
        }
        result.setdefault("ok", True)
        print(json.dumps(result))
        return

    persons = inference_data.get("persons", {})
    persons_dict = persons.get("persons", {})

    timestamp_tracks = {}

    if isinstance(persons_dict, dict):
        for track_id, track_data in persons_dict.items():
            if not isinstance(track_data, dict):
                continue
            windows = track_data.get("windows", [])
            if not isinstance(windows, list):
                continue
            for win in windows:
                if not isinstance(win, dict):
                    continue
                t = win.get("t")
                if not isinstance(t, (int, float)):
                    continue
                kept = win.get("kept", True)
                if kept:
                    if t not in timestamp_tracks:
                        timestamp_tracks[t] = set()
                    timestamp_tracks[t].add(str(track_id))
    elif isinstance(persons_dict, list):
        for track_data in persons_dict:
            if not isinstance(track_data, dict):
                continue
            track_id = track_data.get("trackId", track_data.get("id"))
            windows = track_data.get("windows", [])
            if not isinstance(windows, list):
                continue
            for win in windows:
                if not isinstance(win, dict):
                    continue
                t = win.get("t")
                if not isinstance(t, (int, float)):
                    continue
                kept = win.get("kept", True)
                if kept:
                    if t not in timestamp_tracks:
                        timestamp_tracks[t] = set()
                    timestamp_tracks[t].add(str(track_id))

    peak_count = 0
    if timestamp_tracks:
        peak_count = max(len(tracks) for tracks in timestamp_tracks.values())

    answer_text = f"up to {peak_count} people were observed at once during the recording."

    caveats = [
        f"Selected camera: {selected_cam}",
        f"Selected model: {selected_model}",
        "The result is peak concurrent anonymous tracks."
    ]
    if not timestamp_tracks:
        caveats.append("No valid windows with numeric 't' were found in the inference data.")

    result["answer"] = {
        "answer": answer_text,
        "confidence": 0.9 if timestamp_tracks else 0.4,
        "evidence_refs": [inference_url],
        "caveats": caveats,
        "privacy_scope": "cityos_structured_context_only",
        "limitations": ["Count is based on anonymous tracks, not identities."]
    }
    result["evidence"] = [{
        "type": "inference",
        "url": inference_url,
        "day": selected_rec.get("day"),
        "rec": selected_rec.get("rec")
    }]

    result.setdefault("ok", True)

    print(json.dumps(result))
if __name__ == "__main__":
    main()
