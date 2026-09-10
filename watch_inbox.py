"""inbox 폴더 감시: 영상 파일이 들어오면 자동으로 파이프라인을 돌린다.

    python watch_inbox.py            (기본: ./inbox 감시, 결과는 ./output)
    python watch_inbox.py D:/촬영본   (다른 폴더 감시)

처리 후 원본은 inbox/done/ 으로, 실패하면 inbox/failed/ 로 옮긴다.
"""
from __future__ import annotations

import logging
import shutil
import sys
import threading
import time
from pathlib import Path
from queue import Queue

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from video_pipeline.config import ROOT, PipelineConfig
from video_pipeline.pipeline import VIDEO_EXTS, process_video
from video_pipeline.utils import log, setup_logging


def wait_until_stable(path: Path, quiet_sec: float = 5.0, timeout: float = 3600) -> bool:
    """복사/업로드가 끝나 파일 크기가 더 이상 변하지 않을 때까지 대기."""
    t0 = time.time()
    last, last_change = -1, time.time()
    while time.time() - t0 < timeout:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size != last:
            last, last_change = size, time.time()
        elif time.time() - last_change >= quiet_sec and size > 0:
            try:
                with open(path, "rb"):
                    return True
            except PermissionError:
                pass
        time.sleep(1.0)
    return False


class Handler(FileSystemEventHandler):
    def __init__(self, q: Queue):
        self.q = q

    def _enqueue(self, p: str):
        path = Path(p)
        if path.suffix.lower() in VIDEO_EXTS and path.parent.name not in ("done", "failed"):
            self.q.put(path)

    def on_created(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._enqueue(event.dest_path)


def worker(q: Queue, cfg: PipelineConfig, inbox: Path):
    done_dir, failed_dir = inbox / "done", inbox / "failed"
    done_dir.mkdir(exist_ok=True)
    failed_dir.mkdir(exist_ok=True)
    seen: set[Path] = set()
    while True:
        path: Path = q.get()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        log.info(f"새 영상 감지: {path.name} — 복사 완료 대기 중")
        if not wait_until_stable(path):
            log.warning(f"파일이 안정되지 않아 건너뜀: {path.name}")
            continue
        try:
            process_video(path, cfg)
            shutil.move(str(path), str(done_dir / path.name))
        except Exception as e:
            log.exception(f"실패: {path.name} — {e}")
            try:
                shutil.move(str(path), str(failed_dir / path.name))
                (failed_dir / (path.stem + ".error.txt")).write_text(str(e), encoding="utf-8")
            except Exception:
                pass


def main():
    setup_logging(logging.INFO)
    inbox = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    cfg = PipelineConfig()
    q: Queue = Queue()
    threading.Thread(target=worker, args=(q, cfg, inbox), daemon=True).start()

    # 이미 들어있는 파일도 처리
    for p in sorted(inbox.iterdir()):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            q.put(p)

    obs = Observer()
    obs.schedule(Handler(q), str(inbox), recursive=False)
    obs.start()
    log.info(f"감시 중: {inbox}  (영상을 이 폴더에 넣으면 자동 편집됩니다. 종료: Ctrl+C)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()


if __name__ == "__main__":
    main()
