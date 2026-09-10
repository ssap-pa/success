"""사용법:
    python -m video_pipeline 영상.mp4
    python -m video_pipeline 폴더/            (폴더 안 모든 영상)
    python -m video_pipeline 영상.mp4 --mode pip --no-mosaic --fresh
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import PipelineConfig
from .drive_upload import folder_id_from, upload_outputs
from .pipeline import VIDEO_EXTS, process_video
from .utils import log, setup_logging


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="video_pipeline", description="유튜브 영상 1차 자동 편집")
    p.add_argument("inputs", nargs="+", help="영상 파일 또는 폴더")
    p.add_argument("--out", help="결과 폴더 (기본: ./output)")
    p.add_argument("--mode", choices=["center", "pip", "cutaway"], help="일러스트 방식: 중앙 카드 / 우측 카드 / 전체 전환")
    p.add_argument("--whisper-model", help="예: large-v3, medium, small")
    p.add_argument("--no-illustrations", action="store_true", help="설명 일러스트 생략")
    p.add_argument("--no-overlays", action="store_true", help="로고·아이콘 오버레이 생략")
    p.add_argument("--steps", help="특정 단계만 실행 (쉼표 구분): transcribe,cut,plan,assets,pii,render")
    p.add_argument("--no-mosaic", action="store_true", help="개인정보 모자이크 생략")
    p.add_argument("--no-cut", action="store_true", help="무음/추임새 컷 생략")
    p.add_argument("--blur-faces", action="store_true", help="얼굴도 모자이크")
    p.add_argument("--mute-spoken-pii", action="store_true", help="말로 나온 개인정보 음소거")
    p.add_argument("--image-provider", choices=["auto", "openai", "placeholder"])
    p.add_argument("--fresh", action="store_true", help="캐시 무시하고 처음부터")
    p.add_argument("--drive-folder", help="결과(mp4·srt·md)를 올릴 Google Drive 폴더 ID 또는 URL (GDRIVE_SERVICE_ACCOUNT_JSON 필요)")
    p.add_argument("--subtitles", choices=["none", "variety", "clean"],
                   help="자막 번인: variety=흑백요리사 풍 예능 자막(굵은 고딕·검정 외곽선·노란 강조), clean=담백한 흰 자막")
    p.add_argument("--subtitle-emphasis", help="노란색으로 강조할 단어, 쉼표 구분 (예: 곰돌이,말차)")
    p.add_argument("--fix", help="전사 오류 교정, 쉼표 구분 '잘못=바름' (예: 뱃살=곰돌이,마오차=말차). 자막·리포트·기획에 반영")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def apply_args(cfg: PipelineConfig, a: argparse.Namespace) -> PipelineConfig:
    if a.out:
        cfg.out_dir = Path(a.out)
    if a.mode:
        cfg.illustration_mode = a.mode
    if a.whisper_model:
        cfg.whisper_model = a.whisper_model
    if a.no_illustrations:
        cfg.illustrations = False
    if a.no_overlays:
        cfg.overlays = False
    if a.no_mosaic:
        cfg.mosaic = False
    if a.no_cut:
        cfg.min_silence = 10**9
        cfg.filler_words = ()
        cfg.remove_stutters = False
    if a.blur_faces:
        cfg.blur_faces = True
    if a.mute_spoken_pii:
        cfg.mute_spoken_pii = True
    if a.image_provider:
        cfg.image_provider = a.image_provider
    if a.fresh:
        cfg.reuse_cache = False
    if a.drive_folder:
        cfg.drive_folder = a.drive_folder
    if a.subtitles:
        cfg.subtitle_style = a.subtitles
    if a.subtitle_emphasis:
        cfg.subtitle_emphasis = a.subtitle_emphasis
    if a.fix:
        cfg.transcript_fixes = a.fix
    return cfg


def collect_inputs(paths: list[str]) -> list[Path]:
    out = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.suffix.lower() in VIDEO_EXTS)
        elif p.is_file():
            out.append(p)
        else:
            log.warning(f"찾을 수 없음: {p}")
    return out


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if a.verbose else logging.INFO)
    cfg = apply_args(PipelineConfig(), a)
    videos = collect_inputs(a.inputs)
    if not videos:
        log.error("처리할 영상이 없습니다.")
        return 1
    failed = 0
    steps = [s.strip() for s in a.steps.split(",")] if a.steps else None
    folder = folder_id_from(cfg.drive_folder)
    for v in videos:
        try:
            from .pipeline import Job
            if steps:
                job = Job(v, cfg)
                job.run(steps, force=True)
            else:
                process_video(v, cfg)
                job = Job(v, cfg)
            if folder:
                log.info("  ▶ Google Drive 업로드")
                for f in upload_outputs([job.out_video, job.out_srt, job.out_report], folder):
                    log.info(f"    {f.get('name')} → {f.get('webViewLink')}")
        except Exception as e:
            failed += 1
            log.exception(f"실패: {v.name} — {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
