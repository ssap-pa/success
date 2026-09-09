from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


# 추임새(필러워드) 목록. 단독 토큰으로 나올 때만 제거한다.
DEFAULT_FILLERS = (
    "어", "음", "엄", "으", "에", "아", "어어", "음음", "으음", "흠",
    "uh", "um", "hmm", "mm",
)


@dataclass
class PipelineConfig:
    # ── 전사 ──────────────────────────────────────────────
    whisper_model: str = _env("WHISPER_MODEL", "large-v3")
    whisper_device: str = _env("WHISPER_DEVICE", "auto")      # auto | cuda | cpu
    language: str = _env("LANGUAGE", "ko")

    # ── 컷 편집 ───────────────────────────────────────────
    silence_db: float = _env("SILENCE_DB", -35.0)              # 이보다 조용하면 무음
    min_silence: float = _env("MIN_SILENCE", 0.6)              # 이 길이 이상만 편집 대상
    keep_silence: float = _env("KEEP_SILENCE", 0.3)            # 긴 무음을 이 길이로 줄임
    filler_words: tuple = DEFAULT_FILLERS
    filler_max_dur: float = 1.2                                 # 이보다 길게 발화되면 추임새 아님
    remove_stutters: bool = _env("REMOVE_STUTTERS", True)      # "그 그 그" 반복 제거
    min_cut: float = 0.08                                       # 너무 짧은 컷은 무시
    min_keep: float = 0.15                                      # 너무 짧은 유지 구간은 병합
    edge_pad: float = 0.03                                      # 단어 경계 여유

    # ── 설명 화면(일러스트) ───────────────────────────────
    illustrations: bool = _env("ILLUSTRATIONS", True)
    illustration_mode: str = _env("ILLUSTRATION_MODE", "overlay")   # overlay(투명 스티커) | center | pip | cutaway
    overlays: bool = _env("OVERLAYS", True)                     # 투명 아이콘/로고 오버레이
    max_scenes_per_min: float = _env("MAX_SCENES_PER_MIN", 1.5)
    max_overlays_per_min: float = _env("MAX_OVERLAYS_PER_MIN", 4.0)
    scene_height: float = _env("SCENE_HEIGHT", 0.62)            # 일러스트 스티커 크기 (화면 높이 비율)
    overlay_height: float = _env("OVERLAY_HEIGHT", 0.32)        # 아이콘/로고 스티커 크기 (화면 높이 비율)
    scene_position: str = _env("SCENE_POSITION", "center")      # 일러스트 위치
    anim_in: float = 0.3                                        # 등장 애니메이션(초)
    anim_out: float = 0.25                                      # 퇴장 애니메이션(초)
    sfx: bool = _env("SFX", True)                               # 등장/퇴장 효과음
    sfx_volume: float = _env("SFX_VOLUME", 0.35)
    scene_min_dur: float = 4.0
    scene_max_dur: float = 8.0
    fade: float = 0.4
    claude_model: str = _env("CLAUDE_MODEL", "claude-opus-5")
    image_provider: str = _env("IMAGE_PROVIDER", "auto")       # auto | openai | placeholder
    image_model: str = _env("IMAGE_MODEL", "gpt-image-1")
    image_quality: str = _env("IMAGE_QUALITY", "medium")
    image_workers: int = _env("IMAGE_WORKERS", 5)              # 이미지 동시 생성 수 (OpenAI 병렬 요청)
    logo_dir: Path = field(default_factory=lambda: Path(_env("LOGO_DIR", str(ROOT / "로고"))))

    # ── 개인정보 모자이크 ─────────────────────────────────
    mosaic: bool = _env("MOSAIC", True)
    ocr_interval: float = _env("OCR_INTERVAL", 0.5)            # 몇 초마다 화면 검사
    ocr_langs: tuple = ("ko", "en")
    ocr_min_conf: float = 0.2
    ocr_max_width: int = _env("OCR_MAX_WIDTH", 1280)           # 이 폭으로 줄여서 OCR (0=원본 해상도)
    ocr_skip_static: bool = _env("OCR_SKIP_STATIC", True)      # 화면이 안 바뀌면 이전 결과 재사용
    ocr_change_threshold: float = _env("OCR_CHANGE_THRESHOLD", 1.5)   # 화면 변화 감지 민감도(작을수록 민감)
    ocr_force_interval: float = _env("OCR_FORCE_INTERVAL", 3.0)       # 정적이어도 최소 이 주기로는 OCR
    ocr_batch_size: int = _env("OCR_BATCH_SIZE", 8)             # 글자 인식 배치 크기
    mosaic_block: int = 16                                      # 모자이크 픽셀 크기
    mosaic_margin: int = 10                                     # 박스 여유(px)
    region_merge_gap: float = 1.2                               # 탐지 끊김 허용(초)
    blur_faces: bool = _env("BLUR_FACES", False)               # 얼굴은 기본 OFF (본인 얼굴)
    mute_spoken_pii: bool = _env("MUTE_SPOKEN_PII", False)     # 말로 나온 개인정보 음소거

    # ── 인코딩 ────────────────────────────────────────────
    video_codec: str = _env("VIDEO_CODEC", "auto")             # auto | h264_nvenc | libx264
    quality: int = _env("VIDEO_QUALITY", 19)                    # crf / cq

    # ── 경로 ──────────────────────────────────────────────
    out_dir: Path = field(default_factory=lambda: Path(_env("OUT_DIR", str(ROOT / "output"))))
    work_dir: Path = field(default_factory=lambda: Path(_env("WORK_DIR", str(ROOT / "work"))))
    upload_dir: Path = field(default_factory=lambda: Path(_env("UPLOAD_DIR", str(ROOT / "uploads"))))
    reuse_cache: bool = True
    drive_folder: str = _env("GDRIVE_FOLDER_ID", "")   # 결과를 올릴 Google Drive 폴더 ID (서비스 계정)
    web_port: int = _env("WEB_PORT", 8766)   # 8765는 이 PC의 다른 앱(autotube)이 사용 중

    def update(self, **kw) -> "PipelineConfig":
        for k, v in kw.items():
            if v is None or not hasattr(self, k):
                continue
            cur = getattr(self, k)
            if isinstance(cur, Path):
                v = Path(v)
            elif isinstance(cur, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int) and not isinstance(cur, bool):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            setattr(self, k, v)
        return self
