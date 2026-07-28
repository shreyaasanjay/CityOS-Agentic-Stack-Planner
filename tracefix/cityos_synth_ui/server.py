"""Standalone local website for synthesizing TraceFix workspaces into CityOS apps."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from tracefix.runtime.cityos_synthesizer import synthesize_cityos_apps
from tracefix.runtime.web_data_harness import default_web_data_output_root, default_web_data_url, run_web_data_apps
from tracefix.textio import safe_read_json


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True

def _default_cityos_root() -> Path | None:
    configured = os.environ.get("CITYOS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    candidate = Path.home() / "cityos"
    return candidate if candidate.exists() else None


def _spec_dir(workspace: Path) -> Path:
    spec = workspace / "spec"
    return spec if spec.is_dir() else workspace


def _workspace_summary(workspace: Path) -> dict[str, Any]:
    spec = _spec_dir(workspace)
    plan_path = spec / "cityos_module_plan.json"
    summary = safe_read_json(spec / "summary.json", {})
    plan = safe_read_json(plan_path, {})
    verification = plan.get("verification", {}) if isinstance(plan, dict) else {}
    return {
        "name": workspace.name,
        "path": str(workspace),
        "lastModified": workspace.stat().st_mtime,
        "planPath": str(plan_path),
        "hasPlan": plan_path.exists(),
        "tlcPassed": summary.get("tlc_passed") if isinstance(summary, dict) else None,
        "productionReady": verification.get("production_ready") is True,
        "verificationStatus": verification.get("status") or (
            "verified" if isinstance(summary, dict) and summary.get("tlc_passed") is True else "unknown"
        ),
        "agents": [agent.get("name") for agent in plan.get("agents", [])]
        if isinstance(plan, dict) and isinstance(plan.get("agents"), list) else [],
    }


def _workspace_options(root: Path) -> list[dict[str, Any]]:
    workspace_root = root / "workspace"
    if not workspace_root.exists():
        return []
    workspaces = [path for path in workspace_root.iterdir() if path.is_dir()]
    workspaces.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [_workspace_summary(path) for path in workspaces[:80]]


def _resolve_workspace(root: Path, raw: str) -> Path:
    value = Path(raw.strip()).expanduser()
    if not value.is_absolute():
        value = root / value
    return value.resolve()


def _resolve_apps_dir(payload: dict[str, Any]) -> Path:
    raw_apps = str(payload.get("appsDir") or "").strip()
    if raw_apps:
        return Path(raw_apps).expanduser().resolve()
    raw_cityos = str(payload.get("cityosRoot") or "").strip()
    if raw_cityos:
        return (Path(raw_cityos).expanduser() / "apps").resolve()
    default_root = _default_cityos_root()
    if default_root is not None:
        return (default_root / "apps").resolve()
    raise ValueError("CityOS root or apps directory is required")


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: int, field: str) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


class SynthHandler(BaseHTTPRequestHandler):
    server_version = "TraceFixCityOSSynth/0.1"

    @property
    def root(self) -> Path:
        return self.server.root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[tracefix-cityos-synth] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            cityos_root = _default_cityos_root()
            self._send_json({
                "repoRoot": str(self.root),
                "cityosRoot": str(cityos_root) if cityos_root is not None else "",
                "appsDir": str((cityos_root / "apps").resolve()) if cityos_root is not None else "",
                "webDataUrl": default_web_data_url(),
                "workspaces": _workspace_options(self.root),
            })
            return
        if parsed.path == "/api/workspaces":
            self._send_json({"workspaces": _workspace_options(self.root)})
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            self._send_file(STATIC_DIR / "index.html")
            return
        if parsed.path.startswith("/static/"):
            rel = unquote(parsed.path.removeprefix("/static/"))
            self._send_file(STATIC_DIR / rel)
            return
        self._send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/synthesize":
                self._handle_synthesize(payload)
                return
            if parsed.path in {"/api/run-web-data", "/api/synth/run-web-data"}:
                self._handle_run_web_data(payload)
                return
            self._send_error(404, "Not Found")
        except Exception as exc:  # noqa: BLE001 - local website should surface clear errors
            self._send_error(400, str(exc))

    def _handle_synthesize(self, payload: dict[str, Any]) -> None:
        workspace_raw = str(payload.get("workspacePath") or "").strip()
        if not workspace_raw:
            raise ValueError("TraceFix workspace is required")
        workspace = _resolve_workspace(self.root, workspace_raw)
        apps_dir = _resolve_apps_dir(payload)
        package_name = str(payload.get("packageName") or "").strip() or None
        overwrite = bool(payload.get("overwrite"))
        result = synthesize_cityos_apps(
            workspace,
            apps_dir=apps_dir,
            package_name=package_name,
            overwrite=overwrite,
        )
        self._send_json({
            "workspace": str(result.workspace),
            "planPath": str(result.plan_path),
            "appsDir": str(result.apps_dir),
            "manifestPath": str(result.manifest_path),
            "apps": [
                {
                    "name": app.name,
                    "kind": app.kind,
                    "agent": app.agent,
                    "path": str(app.path),
                    "buildCommand": f"just build {app.name}",
                }
                for app in result.apps
            ],
        })

    def _handle_run_web_data(self, payload: dict[str, Any]) -> None:
        manifest_raw = str(payload.get("manifestPath") or "").strip()
        if not manifest_raw:
            raise ValueError("Synthesis manifest path is required")
        manifest_path = Path(manifest_raw).expanduser()
        if not manifest_path.is_absolute():
            manifest_path = self.root / manifest_path
        manifest_path = manifest_path.resolve()
        if not manifest_path.exists():
            raise FileNotFoundError(f"Synthesis manifest does not exist: {manifest_path}")

        source_url = str(payload.get("sourceUrl") or default_web_data_url()).strip()
        source_mode = str(payload.get("sourceMode") or "auto").strip() or "auto"
        timeout_seconds = _bounded_int(
            payload.get("timeoutSeconds"),
            30,
            minimum=1,
            maximum=300,
            field="timeoutSeconds",
        )
        handler_timeout_seconds = _bounded_int(
            payload.get("handlerTimeoutSeconds"),
            60,
            minimum=1,
            maximum=600,
            field="handlerTimeoutSeconds",
        )
        max_bytes = _bounded_int(
            payload.get("maxBytes"),
            50 * 1024 * 1024,
            minimum=1024,
            maximum=500 * 1024 * 1024,
            field="maxBytes",
        )
        output_root = None
        output_raw = str(payload.get("outputRoot") or "").strip()
        if output_raw:
            output_root = Path(output_raw).expanduser()
            if not output_root.is_absolute():
                output_root = self.root / output_root
            output_root = output_root.resolve()
        if output_root is None or not _path_is_within(output_root, self.root):
            output_root = default_web_data_output_root(manifest_path, repo_root=self.root)
        raw_data_json = str(
            payload.get("rawDataJson")
            or payload.get("raw_data_json")
            or payload.get("rawJson")
            or ""
        )
        result = run_web_data_apps(
            manifest_path=manifest_path,
            source_url=source_url,
            output_root=output_root,
            source_mode=source_mode,
            timeout_seconds=timeout_seconds,
            handler_command=payload.get("handlerCommand") or None,
            handler_timeout_seconds=handler_timeout_seconds,
            max_bytes=max_bytes,
            raw_data_json=raw_data_json,
        )
        self._send_json(result)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        data = self.rfile.read(length)
        if not data:
            return {}
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("expected JSON object")
        return parsed

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(404, "Not Found")
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)


def run_server(host: str = "127.0.0.1", port: int = 8790, root: Path | None = None) -> None:
    repo_root = (root or _repo_root()).resolve()
    server = ThreadingHTTPServer((host, port), SynthHandler)
    server.root = repo_root  # type: ignore[attr-defined]
    print(f"TraceFix CityOS Synthesizer running at http://{host}:{port}")
    print(f"TraceFix repo root: {repo_root}")
    print(f"Default CityOS root: {_default_cityos_root()}")
    print(f"Python: {sys.executable}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTraceFix CityOS Synthesizer stopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the TraceFix CityOS Synthesizer website")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port, root=args.root)


if __name__ == "__main__":
    main()
