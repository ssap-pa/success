"""최종 렌더: 컷 편집본 위에 모자이크 + 투명 스티커(일러스트·아이콘·로고) + 효과음을 얹어 ffmpeg로 인코딩.

- illustration_mode="overlay"(기본): 일러스트도 카드 없이 투명 스티커로 화면 중앙에 크게 얹는다.
- center/pip/cutaway: 예전 카드 방식 (옵션).
- 모든 스티커는 등장 시 팝(살짝 커졌다 제자리) + 페이드, 퇴장 시 축소 + 페이드. 각 순간에 효과음.
"""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .illustrator import LogoIndex, build_card, build_illustration_sprite, build_sticker
from .pii import Region, pixelate
from .planner import Overlay, Scene
from .utils import FFMPEG, codec_args, log, probe


# ── 애니메이션 ────────────────────────────────────────────────

def _ease_out_back(p: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def _anim(t: float, start: float, end: float, ain: float, aout: float) -> tuple[float, float] | None:
    """(alpha, scale) 또는 화면에 없으면 None."""
    if t < start or t > end:
        return None
    if t - start < ain:
        p = (t - start) / ain
        return min(1.0, p * 1.6), 0.55 + 0.45 * _ease_out_back(p)
    if end - t < aout:
        p = (end - t) / aout
        return p, 0.8 + 0.2 * p
    return 1.0, 1.0


def _blend(frame: np.ndarray, sprite: np.ndarray, alpha: np.ndarray, x: int, y: int, a: float) -> None:
    H, W = frame.shape[:2]
    h, w = sprite.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sx, sy = x0 - x, y0 - y
    roi = frame[y0:y1, x0:x1]
    sp = sprite[sy:sy + (y1 - y0), sx:sx + (x1 - x0)]
    m = (alpha[sy:sy + (y1 - y0), sx:sx + (x1 - x0)] * a)[:, :, None]
    roi[:] = (sp * m + roi * (1 - m)).astype(np.uint8)


def _draw_sprite(frame, sprite, alpha, cx: int, cy: int, a: float, s: float) -> None:
    """중심 (cx, cy)에 배율 s로 그린다."""
    h, w = sprite.shape[:2]
    if abs(s - 1.0) > 0.005:
        sw, sh = max(1, int(w * s)), max(1, int(h * s))
        inter = cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR
        sprite = cv2.resize(sprite, (sw, sh), interpolation=inter)
        alpha = cv2.resize(alpha, (sw, sh), interpolation=inter)
        w, h = sw, sh
    _blend(frame, sprite, alpha, cx - w // 2, cy - h // 2, a)


def _center_for(pos: str, W: int, H: int, w: int, h: int) -> tuple[int, int]:
    mx, my = int(W * 0.035), int(H * 0.06)
    if pos == "center":
        return W // 2, H // 2
    side = pos.split("-")[-1]
    cx = mx + w // 2 if side == "left" else W - mx - w // 2
    if pos in ("left", "right"):
        cy = H // 2
    elif pos.startswith("top"):
        cy = my + h // 2
    else:
        cy = H - my - h // 2
    return cx, cy


# ── 스프라이트 준비 ───────────────────────────────────────────

class Sprite:
    def __init__(self, start, end, bgr, alpha, cx, cy, big: bool):
        self.start, self.end, self.bgr, self.alpha, self.cx, self.cy, self.big = start, end, bgr, alpha, cx, cy, big


def _prepare_sprites(scenes: list[Scene], overlays: list[Overlay], W: int, H: int, cfg) -> list[Sprite]:
    out: list[Sprite] = []
    if cfg.illustration_mode == "overlay":
        for sc in scenes:
            img = Image.open(sc.image_path) if sc.image_path and Path(sc.image_path).exists() else None
            bgr, alpha = build_illustration_sprite(img, sc.label, H, cfg)
            h, w = bgr.shape[:2]
            cx, cy = _center_for(cfg.scene_position, W, H, w, h)
            out.append(Sprite(sc.start, sc.end, bgr, alpha, cx, cy, True))
    for ov in overlays:
        if not ov.image_path or not Path(ov.image_path).exists():
            continue
        bgr, alpha = build_sticker(Image.open(ov.image_path), int(H * cfg.overlay_height))
        h, w = bgr.shape[:2]
        cx, cy = _center_for(ov.position, W, H, w, h)
        out.append(Sprite(ov.start, ov.end, bgr, alpha, cx, cy, False))
    return out


def _prepare_cards(scenes: list[Scene], W: int, H: int, cfg):
    """카드 방식(center/pip/cutaway)일 때만 사용."""
    logos = LogoIndex(cfg.logo_dir)
    out = []
    for sc in scenes:
        img = Image.open(sc.image_path) if sc.image_path and Path(sc.image_path).exists() else None
        logo = logos.find(sc.logo_hint) if sc.logo_hint else None
        bgr, alpha = build_card(img, sc.label, logo, W, H, cfg.illustration_mode)
        h, w = bgr.shape[:2]
        x, y = (W - w - int(W * 0.03), (H - h) // 2) if cfg.illustration_mode == "pip" else ((W - w) // 2, (H - h) // 2)
        out.append((sc, bgr, alpha, x, y))
    return out


def _fade(t, start, end, fade):
    if t < start or t > end:
        return 0.0
    return 1.0 if fade <= 0 else max(0.0, min(1.0, (t - start) / fade, (end - t) / fade))


# ── 렌더 ─────────────────────────────────────────────────────

def render_final(cut_video: Path, out: Path, scenes: list[Scene], overlays: list[Overlay],
                 regions: list[Region], mute_ranges: list[tuple[float, float]], cfg,
                 ass_path: Path | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    info = probe(cut_video)
    W, H, fps, duration = info["width"], info["height"], info["fps"], info["duration"]

    if not scenes and not overlays and not regions and not mute_ranges and not ass_path:
        log.info("  덧입힐 요소가 없어 컷 편집본을 결과로 사용합니다.")
        shutil.copyfile(cut_video, out)
        return out

    sprites = _prepare_sprites(scenes, overlays, W, H, cfg)
    cards = _prepare_cards(scenes, W, H, cfg) if cfg.illustration_mode != "overlay" else []
    log.info(f"  스티커 {len(sprites)}개" + (f", 카드 {len(cards)}개" if cards else "") +
             f", 모자이크 영역 {len(regions)}개")

    # 효과음 트랙
    sfx_path = None
    if cfg.sfx and (sprites or cards):
        from .sfx import build_track
        events = []
        for sp in sprites:
            events += [(sp.start, "in_big" if sp.big else "in_small"),
                       (sp.end - cfg.anim_out, "out_big" if sp.big else "out_small")]
        for sc, *_ in cards:
            events += [(sc.start, "in_big"), (sc.end - cfg.fade, "out_big")]
        sfx_path = build_track(events, duration, out.with_suffix(".sfx.wav"), cfg.sfx_volume)
        log.info(f"  효과음 {len(events)}개 삽입")

    # ffmpeg: 0=가공 프레임(pipe), 1=컷 편집본(오디오), 2=효과음
    cmd = [FFMPEG, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "pipe:0",
           "-i", str(cut_video)]
    if sfx_path:
        cmd += ["-i", str(sfx_path)]
    # 필터 그래프: 자막(libass)은 가공 프레임 위에, 오디오는 음소거·효과음 믹스
    graph, vmap = [], "0:v:0"
    if ass_path:
        from .subtitles import ffmpeg_filter_arg
        graph.append(f"[0:v:0]ass={ffmpeg_filter_arg(ass_path)}[vout]")
        vmap = "[vout]"
        log.info("  자막 번인: " + ass_path.name)
    amap, aextra = None, []
    if info["has_audio"]:
        mute = ",".join(f"volume=enable='between(t,{s:.3f},{e:.3f})':volume=0" for s, e in mute_ranges) or "anull"
        if sfx_path:
            graph += [f"[1:a]{mute}[a0]", "[2:a]anull[a1]",
                      "[a0][a1]amix=inputs=2:duration=first:normalize=0[aout]"]
            amap, aextra = "[aout]", ["-c:a", "aac", "-b:a", "192k"]
        elif mute_ranges:
            graph.append(f"[1:a:0]{mute}[aout]")
            amap, aextra = "[aout]", ["-c:a", "aac", "-b:a", "192k"]
        else:
            amap, aextra = "1:a:0", ["-c:a", "copy"]
    elif sfx_path:
        amap, aextra = "2:a:0", ["-c:a", "aac", "-b:a", "128k"]
    if graph:
        cmd += ["-filter_complex", ";".join(graph)]
    cmd += ["-map", vmap, *codec_args(cfg)]
    if amap:
        cmd += ["-map", amap, *aextra]
    cmd += ["-shortest", "-movflags", "+faststart", str(out)]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    cap = cv2.VideoCapture(str(cut_video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    idx, last_pct = 0, -1
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = idx / fps
            for r in regions:
                if r.start <= t <= r.end:
                    pixelate(frame, r.x1, r.y1, r.x2, r.y2, cfg.mosaic_block, cfg.mosaic_margin)
            for sc, card, alpha, x, y in cards:
                a = _fade(t, sc.start, sc.end, cfg.fade)
                if a <= 0:
                    continue
                if cfg.illustration_mode == "cutaway":
                    frame = card.copy() if a >= 0.999 else cv2.addWeighted(card, a, frame, 1 - a, 0)
                else:
                    _blend(frame, card, alpha, x, y, a)
            for sp in sprites:
                st = _anim(t, sp.start, sp.end, cfg.anim_in, cfg.anim_out)
                if st is None:
                    continue
                a, s = st
                _draw_sprite(frame, sp.bgr, sp.alpha, sp.cx, sp.cy, a, s)
            proc.stdin.write(frame.tobytes())
            idx += 1
            if total:
                pct = int(idx * 100 / total)
                if pct // 20 != last_pct // 20:
                    log.info(f"  렌더 {pct}%")
                    last_pct = pct
    finally:
        cap.release()
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 렌더 실패: {err[-800:]}")
    if sfx_path and Path(sfx_path).exists():
        Path(sfx_path).unlink()
    return out
