"""로컬 웹앱: 영상 업로드 → 버튼으로 단계별 실행 → 결과 확인/기획 수정.

    python -m web.server            (http://127.0.0.1:8765)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from video_pipeline.config import ROOT, PipelineConfig
from video_pipeline.pipeline import STEP_LABELS, STEPS, VIDEO_EXTS, Job, safe_stem
from video_pipeline.utils import log, setup_logging

STATIC = Path(__file__).parent / "static"
ENV_PATH = ROOT / ".env"
SETTING_KEYS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WHISPER_MODEL", "CLAUDE_MODEL", "IMAGE_MODEL",
                "IMAGE_QUALITY"]

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 ** 3      # 20GB
setup_logging(logging.INFO)

# ── 작업 상태 (한 번에 하나만 실행) ─────────────────────────────
_lock = threading.Lock()
_job: dict = {"id": 0, "status": "idle", "video": "", "steps": [], "log": [], "started": 0, "ended": 0,
              "error": ""}


class _LogHandler(logging.Handler):
    def emit(self, record):
        if _job["status"] == "running":
            _job["log"].append(self.format(record))


_h = _LogHandler()
_h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
log.addHandler(_h)


def base_config() -> PipelineConfig:
    cfg = PipelineConfig()
    cfg.upload_dir.mkdir(parents=True, exist_ok=True)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    return cfg


OPTION_FIELDS = {
    "illustration_mode": str, "illustrations": bool, "overlays": bool, "mosaic": bool,
    "blur_faces": bool, "mute_spoken_pii": bool, "whisper_model": str, "image_provider": str,
    "min_silence": float, "keep_silence": float, "max_scenes_per_min": float,
    "max_overlays_per_min": float, "ocr_interval": float, "video_quality": int,
    "ocr_max_width": int, "ocr_skip_static": bool, "ocr_force_interval": float,
    "sfx": bool, "sfx_volume": float, "scene_height": float, "overlay_height": float, "scene_position": str,
}


def config_from_options(opts: dict) -> PipelineConfig:
    cfg = base_config()
    clean = {}
    for k, typ in OPTION_FIELDS.items():
        if k in opts and opts[k] is not None and opts[k] != "":
            clean[k if k != "video_quality" else "quality"] = opts[k]
    cfg.update(**clean)
    if opts.get("cut") is False:
        cfg.min_silence = 10 ** 9
        cfg.filler_words = ()
        cfg.remove_stutters = False
    return cfg


def list_videos(cfg: PipelineConfig) -> list[dict]:
    seen, out = set(), []
    for d in (cfg.upload_dir, ROOT / "inbox", ROOT / "samples"):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.resolve() not in seen:
                seen.add(p.resolve())
                try:
                    st = Job(p, cfg).state()
                except Exception:
                    st = {"stem": safe_stem(p)}
                out.append({"name": p.name, "path": str(p), "folder": d.name,
                            "size_mb": round(p.stat().st_size / 1e6, 1), **st})
    return out


# ── 페이지 / 파일 ─────────────────────────────────────────────
@app.after_request
def _no_cache(resp):
    # 코드를 고치고 재시작했을 때 브라우저가 옛 화면/옛 옵션을 쓰지 않도록
    if request.path == "/" or request.path.startswith("/api/") or request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/version")
def api_version():
    from video_pipeline import __version__
    return jsonify({"version": __version__, "started": _STARTED})


@app.get("/files/output/<path:name>")
def file_output(name):
    return send_from_directory(base_config().out_dir, name, conditional=True)


@app.get("/files/work/<stem>/<path:name>")
def file_work(stem, name):
    return send_from_directory(base_config().work_dir / stem, name, conditional=True)


@app.get("/files/video")
def file_video():
    p = Path(request.args.get("path", "")).resolve()
    if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
        abort(404)
    return send_file(p, conditional=True)


# ── API ───────────────────────────────────────────────────────
@app.get("/api/videos")
def api_videos():
    return jsonify(list_videos(base_config()))


@app.post("/api/upload")
def api_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "파일이 없습니다"}), 400
    name = re.sub(r"[\\/:*?\"<>|]+", "_", f.filename)
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        return jsonify({"error": "영상 파일만 올릴 수 있습니다"}), 400
    cfg = base_config()
    dest = cfg.upload_dir / name
    i = 1
    while dest.exists():
        dest = cfg.upload_dir / f"{Path(name).stem}_{i}{Path(name).suffix}"
        i += 1
    f.save(dest)
    return jsonify({"ok": True, "path": str(dest), "name": dest.name})


@app.post("/api/run")
def api_run():
    data = request.get_json(force=True) or {}
    path = Path(data.get("path", ""))
    steps = data.get("steps") or STEPS
    force = bool(data.get("force", False))
    if not path.is_file():
        return jsonify({"error": "영상 파일을 찾을 수 없습니다"}), 400
    bad = [s for s in steps if s not in STEPS]
    if bad:
        return jsonify({"error": f"알 수 없는 단계: {bad}"}), 400
    with _lock:
        if _job["status"] == "running":
            return jsonify({"error": "이미 작업이 실행 중입니다"}), 409
        _job.update(id=_job["id"] + 1, status="running", video=path.name, steps=steps, log=[],
                    started=time.time(), ended=0, error="")
    cfg = config_from_options(data.get("options") or {})

    def worker():
        try:
            job = Job(path, cfg)
            i = job.info
            log.info(f"━━ {path.name}  {i['width']}x{i['height']} {i['fps']:.1f}fps {i['duration']:.1f}s")
            log.info("  단계: " + " → ".join(STEP_LABELS[s] for s in steps) + ("  (강제 재실행)" if force else ""))
            job.run(steps, force=force)
            _job["status"] = "done"
        except Exception as e:
            _job["error"] = str(e)
            _job["log"].append("오류: " + str(e))
            _job["log"].append(traceback.format_exc()[-1500:])
            _job["status"] = "error"
        finally:
            _job["ended"] = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "id": _job["id"]})


@app.get("/api/job")
def api_job():
    since = int(request.args.get("since", 0))
    return jsonify({
        "id": _job["id"], "status": _job["status"], "video": _job["video"], "steps": _job["steps"],
        "log": _job["log"][since:], "total": len(_job["log"]), "error": _job["error"],
        "elapsed": round((_job["ended"] or time.time()) - _job["started"], 1) if _job["started"] else 0,
    })


def _work(stem: str) -> Path:
    d = (base_config().work_dir / stem).resolve()
    if not str(d).startswith(str(base_config().work_dir.resolve())):
        abort(400)
    return d


@app.get("/api/videos/<stem>/plan")
def api_plan_get(stem):
    p = _work(stem) / "plan.json"
    if not p.exists():
        return jsonify({"scenes": [], "overlays": [], "spoken_pii": [], "notes": "",
                        "skipped_reason": "아직 기획하지 않음"})
    return Response(p.read_text(encoding="utf-8"), mimetype="application/json")


@app.put("/api/videos/<stem>/plan")
def api_plan_put(stem):
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON 객체가 필요합니다"}), 400
    from video_pipeline.planner import Plan
    from video_pipeline.utils import save_json
    plan = Plan.from_dict(data)      # 형식 검증
    plan.skipped_reason = ""
    save_json(_work(stem) / "plan.json", plan)
    return jsonify({"ok": True, "scenes": len(plan.scenes), "overlays": len(plan.overlays)})


@app.get("/api/videos/<stem>/report")
def api_report(stem):
    p = base_config().out_dir / f"{stem}_report.md"
    if not p.exists():
        return Response("아직 리포트가 없습니다. 최종 렌더를 실행하세요.", mimetype="text/plain")
    return Response(p.read_text(encoding="utf-8"), mimetype="text/plain; charset=utf-8")


@app.get("/api/videos/<stem>/transcript")
def api_transcript(stem):
    w = _work(stem)
    p = w / "transcript_cut.json" if (w / "transcript_cut.json").exists() else w / "transcript.json"
    if not p.exists():
        return jsonify({"segments": []})
    return Response(p.read_text(encoding="utf-8"), mimetype="application/json")


@app.get("/api/videos/<stem>/cuts")
def api_cuts(stem):
    p = _work(stem) / "cuts.json"
    if not p.exists():
        return jsonify({})
    return Response(p.read_text(encoding="utf-8"), mimetype="application/json")


@app.get("/api/videos/<stem>/pii")
def api_pii(stem):
    p = _work(stem) / "pii.json"
    if not p.exists():
        return jsonify({"regions": []})
    return Response(p.read_text(encoding="utf-8"), mimetype="application/json")


@app.get("/api/videos/<stem>/images")
def api_images(stem):
    d = _work(stem) / "images"
    if not d.is_dir():
        return jsonify([])
    return jsonify(sorted(p.name for p in d.iterdir() if p.suffix.lower() == ".png"))


@app.post("/api/videos/<stem>/clear")
def api_clear(stem):
    if _job["status"] == "running":
        return jsonify({"error": "작업 실행 중에는 지울 수 없습니다"}), 409
    d = _work(stem)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return jsonify({"ok": True})


# ── 설정(.env) ────────────────────────────────────────────────
def _read_env() -> dict:
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if m:
                vals[m.group(1)] = m.group(2)
    return vals


def _mask(v: str) -> str:
    return v if len(v) < 8 else v[:4] + "…" + v[-4:]


@app.get("/api/settings")
def api_settings_get():
    env = _read_env()
    out = {}
    for k in SETTING_KEYS:
        v = os.environ.get(k) or env.get(k, "")
        out[k] = {"set": bool(v), "masked": _mask(v) if k.endswith("_KEY") and v else v}
    cfg = PipelineConfig()
    out["defaults"] = {k: (str(getattr(cfg, k)) if k != "quality" else cfg.quality)
                       for k in ("illustration_mode", "whisper_model", "min_silence", "keep_silence",
                                 "max_scenes_per_min", "max_overlays_per_min", "ocr_interval", "quality",
                                 "scene_height", "overlay_height", "scene_position")}
    out["gpu"] = _gpu_name()
    return jsonify(out)


def _gpu_name() -> str:
    try:
        import torch
        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    except Exception:
        return "?"


@app.post("/api/settings")
def api_settings_post():
    data = request.get_json(force=True) or {}
    env = _read_env()
    for k in SETTING_KEYS:
        if k in data:
            v = str(data[k]).strip()
            if v == "":
                continue                      # 빈 값은 기존 유지
            if v == "__clear__":
                env.pop(k, None)
                os.environ.pop(k, None)
            else:
                env[k] = v
                os.environ[k] = v
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonify({"ok": True})


_STARTED = time.strftime("%Y-%m-%d %H:%M:%S")


def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main():
    cfg = base_config()
    port = cfg.web_port
    url = f"http://127.0.0.1:{port}"
    if not _port_free(port):
        print(f"\n  [!] {port} 포트를 이미 다른 프로그램(이전에 켠 웹앱일 가능성이 큼)이 쓰고 있습니다.")
        print("      web.bat 으로 실행하면 이전 웹앱을 자동으로 종료합니다. 또는 그 창을 닫고 다시 실행하세요.\n")
        sys.exit(1)
    print(f"\n  유튜브 자동 편집 웹앱: {url}   (시작 {_STARTED})\n  (종료: Ctrl+C)\n")
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
