from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

log = logging.getLogger("pipeline")


def setup_logging(level=logging.INFO):
    if log.handlers:
        return log
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(level)
    return log


class Timer:
    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        self.t0 = time.time()
        log.info(f"▶ {self.label}")
        return self

    def __exit__(self, *exc):
        log.info(f"✔ {self.label}  ({time.time() - self.t0:.1f}s)")


def run(cmd: list, check=True, capture=False) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    log.debug("$ " + " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def probe(path: Path) -> dict:
    out = run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture=True,
    ).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise ValueError(f"비디오 스트림이 없습니다: {path}")
    num, den = v.get("r_frame_rate", "30/1").split("/")
    fps = float(num) / float(den or 1)
    duration = float(info["format"].get("duration") or v.get("duration") or 0)
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps,
        "duration": duration,
        "has_audio": a is not None,
        "nb_frames": int(v.get("nb_frames") or 0),
    }


_codec_cache: str | None = None


def pick_video_codec(preferred: str = "auto") -> list[str]:
    """ffmpeg 비디오 인코더 인자 템플릿. NVENC가 있으면 사용."""
    global _codec_cache
    if preferred != "auto":
        codec = preferred
    else:
        if _codec_cache is None:
            enc = run([FFMPEG, "-hide_banner", "-encoders"], capture=True, check=False).stdout
            _codec_cache = "h264_nvenc" if "h264_nvenc" in enc else "libx264"
        codec = _codec_cache
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "{q}", "-b:v", "0",
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "{q}", "-pix_fmt", "yuv420p"]


def codec_args(cfg) -> list[str]:
    return [a.replace("{q}", str(cfg.quality)) for a in pick_video_codec(cfg.video_codec)]


def fmt_srt_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_clock(t: float) -> str:
    m, s = divmod(int(max(0.0, t)), 60)
    return f"{m:02d}:{s:02d}"


def to_jsonable(obj):
    if is_dataclass(obj):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
