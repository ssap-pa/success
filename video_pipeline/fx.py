"""예능 효과(흑백요리사 풍): 급 줌인, 흑백 정지 플래시, 임팩트 자막.

work/<영상>/fx.json 에 이벤트를 적어 두면 렌더 단계가 읽는다. 시각은 컷 편집본 기준(초).

[
  {"kind": "zoom",  "start": 30.1, "end": 32.0, "scale": 1.18},            # 얼굴 쪽으로 급 줌인
  {"kind": "flash", "start": 35.3, "end": 35.9},                            # 흑백+비네트 플래시 + '두둥'
  {"kind": "caption", "start": 30.1, "end": 32.0, "text": "인간 말차 등장", "color": "yellow"}
]

- zoom  : 0.12초 동안 scale 배로 확대(중앙 기준, `cx`,`cy` 0~1로 중심 지정 가능), 끝나기 0.12초 전에 복귀. 효과음 'whip'
- flash : 구간 동안 흑백 + 비네트 + 살짝 확대. 시작 시 효과음 'dudung'
- caption: 화면 상단 중앙 큰 글자(임팩트 자막). color: yellow | white | red. ASS로 그린다
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .utils import log


@dataclass
class FxEvent:
    kind: str                 # zoom | flash | caption
    start: float
    end: float
    text: str = ""
    color: str = "yellow"
    scale: float = 1.15
    cx: float = 0.5
    cy: float = 0.45
    extra: dict = field(default_factory=dict)


def load_fx(path: Path) -> list[FxEvent]:
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("events", [])
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("kind") not in ("zoom", "flash", "caption"):
            continue
        kw = {k: it[k] for k in ("kind", "start", "end", "text", "color", "scale", "cx", "cy") if k in it}
        out.append(FxEvent(**kw))
    out.sort(key=lambda e: e.start)
    log.info(f"  예능 효과 {len(out)}개: " + ", ".join(f"{e.kind}@{e.start:.1f}" for e in out))
    return out


def sfx_events(events: list[FxEvent]) -> list[tuple[float, str]]:
    out = []
    for e in events:
        if e.kind == "zoom":
            out.append((e.start, "whip"))
        elif e.kind == "flash":
            out.append((e.start, "dudung"))
    return out


class FrameFx:
    """프레임 단위 효과(줌·흑백 플래시)를 적용한다. 자막(caption)은 ASS가 담당."""

    ZOOM_IN, ZOOM_OUT = 0.12, 0.12

    def __init__(self, events: list[FxEvent], W: int, H: int):
        self.zooms = [e for e in events if e.kind == "zoom"]
        self.flashes = [e for e in events if e.kind == "flash"]
        self.W, self.H = W, H
        self._vignette = None

    @property
    def active(self) -> bool:
        return bool(self.zooms or self.flashes)

    def _vig(self) -> np.ndarray:
        if self._vignette is None:
            y, x = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
            nx, ny = (x / self.W - 0.5) * 2, (y / self.H - 0.5) * 2
            r = np.sqrt(nx * nx + ny * ny)
            self._vignette = np.clip(1.15 - 0.55 * r * r, 0.35, 1.0)[:, :, None]
        return self._vignette

    @staticmethod
    def _ease(p: float) -> float:
        return 1 - (1 - p) ** 3

    def _zoom_scale(self, t: float) -> tuple[float, float, float] | None:
        for e in self.zooms:
            if e.start <= t <= e.end:
                if t - e.start < self.ZOOM_IN:
                    p = self._ease((t - e.start) / self.ZOOM_IN)
                elif e.end - t < self.ZOOM_OUT:
                    p = self._ease((e.end - t) / self.ZOOM_OUT)
                else:
                    p = 1.0
                return 1.0 + (e.scale - 1.0) * p, e.cx, e.cy
        return None

    def _flash_amount(self, t: float) -> float:
        for e in self.flashes:
            if e.start <= t <= e.end:
                dur = max(e.end - e.start, 0.05)
                p = (t - e.start) / dur
                return 1.0 if p < 0.75 else max(0.0, (1 - p) / 0.25)   # 마지막 25%에서 서서히 복귀
        return 0.0

    def apply(self, frame: np.ndarray, t: float) -> np.ndarray:
        z = self._zoom_scale(t)
        if z and z[0] > 1.002:
            s, cx, cy = z
            w, h = int(self.W / s), int(self.H / s)
            x0 = int(min(max(cx * self.W - w / 2, 0), self.W - w))
            y0 = int(min(max(cy * self.H - h / 2, 0), self.H - h))
            frame = cv2.resize(frame[y0:y0 + h, x0:x0 + w], (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        a = self._flash_amount(t)
        if a > 0:
            gray = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
            gray = (gray.astype(np.float32) * self._vig()).astype(np.uint8)
            frame = gray if a >= 0.999 else cv2.addWeighted(gray, a, frame, 1 - a, 0)
        return frame
