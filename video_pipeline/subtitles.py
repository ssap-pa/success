"""예능 자막(흑백요리사 풍) 번인용 ASS 파일 생성.

컷 편집본 대본(transcript_cut)을 받아 화면 하단 중앙에 굵은 고딕 + 두꺼운 검정 외곽선 +
그림자 자막을 만든다. 강조어(cfg.subtitle_emphasis)는 노란색으로 칠하고, 등장 시 살짝 커지는
팝 + 짧은 페이드를 넣는다. 렌더 단계에서 ffmpeg `ass` 필터(libass)로 프레임에 입힌다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .transcribe import Transcript
from .utils import log

# ASS 색은 &HAABBGGRR& (알파, 파랑, 초록, 빨강)
STYLES = {
    "variety": {                       # 흑백요리사 풍: 흰 굵은 고딕, 검정 외곽선, 노란 강조
        "font": "NanumSquare ExtraBold",
        "size": 0.064,                 # 화면 높이 대비 글자 크기 (4K에서 138px)
        "outline": 0.007,              # 외곽선 두께 (화면 높이 대비)
        "shadow": 0.004,               # 그림자 거리
        "margin_v": 0.07,              # 하단 여백
        "primary": "&H00FFFFFF",       # 흰색
        "outline_color": "&H00000000", # 검정
        "back": "&H80000000",          # 그림자(반투명 검정)
        "emphasis": "&H0000E5FF",      # 노랑 (R=FF G=E5 B=00)
        "pop": True,
    },
    "clean": {                         # 담백한 흰 자막 (강조 없음, 팝 없음)
        "font": "NanumSquare Bold",
        "size": 0.05, "outline": 0.005, "shadow": 0.0, "margin_v": 0.06,
        "primary": "&H00FFFFFF", "outline_color": "&H00000000", "back": "&H00000000",
        "emphasis": "&H00FFFFFF", "pop": False,
    },
}

MIN_DUR = 0.8          # 이보다 짧은 자막은 다음 자막 직전까지 늘린다
GAP = 0.05             # 자막 사이 최소 간격
MAX_CHARS = 20         # 한 줄 최대 글자 수 (넘으면 띄어쓰기에서 두 줄로)


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _fmt(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs, s = 0, s + 1
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap(text: str, max_chars: int = MAX_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars or " " not in text:
        return text
    # 가운데에 가장 가까운 띄어쓰기에서 나눈다
    mid = len(text) // 2
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut].strip() + r"\N" + text[cut + 1:].strip()


def _escape(text: str) -> str:
    return text.replace("{", "｛").replace("}", "｝")


def _emphasize(text: str, words: list[str], color: str) -> str:
    """강조어를 노란색 태그로 감싼다. 긴 단어부터 치환해 부분 겹침을 피한다."""
    if not words:
        return text
    reset = "{\\c&HFFFFFF&}"
    for w in sorted({w.strip() for w in words if w.strip()}, key=len, reverse=True):
        text = re.sub(re.escape(w), lambda m: "{\\c" + color + "&}" + m.group(0) + reset, text)
    return text


def build_cues(tr: Transcript) -> list[Cue]:
    segs = [s for s in tr.segments if s.text.strip()]
    cues: list[Cue] = []
    for i, s in enumerate(segs):
        start, end = float(s.start), float(s.end)
        nxt = float(segs[i + 1].start) if i + 1 < len(segs) else None
        if end - start < MIN_DUR:
            end = start + MIN_DUR
        if nxt is not None:
            end = min(end, nxt - GAP)
        if end <= start:
            end = start + 0.3
        cues.append(Cue(start, end, s.text.strip()))
    return cues


def build_ass(tr: Transcript, width: int, height: int, cfg, path: Path) -> Path:
    st = STYLES.get(cfg.subtitle_style, STYLES["variety"])
    font = cfg.subtitle_font or st["font"]
    size = max(24, round(height * st["size"]))
    outline = max(2, round(height * st["outline"]))
    shadow = round(height * st["shadow"])
    margin_v = round(height * st["margin_v"])
    margin_h = round(width * 0.06)
    emphasis = [w.strip() for w in (cfg.subtitle_emphasis or "").split(",") if w.strip()]

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},{st['primary']},&H000000FF,{st['outline_color']},{st['back']},"
        f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    fx = "{\\fad(60,60)}"
    if st["pop"]:
        fx = "{\\fad(60,60)\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}"

    lines = []
    cues = build_cues(tr)
    for c in cues:
        text = _emphasize(_escape(_wrap(c.text)), emphasis, st["emphasis"])
        lines.append(f"Dialogue: 0,{_fmt(c.start)},{_fmt(c.end)},Default,,0,0,0,,{fx}{text}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8-sig")
    log.info(f"  자막 {len(cues)}줄 ({cfg.subtitle_style}, {font} {size}px"
             + (f", 강조어 {len(emphasis)}개" if emphasis else "") + f") → {path.name}")
    return path


def ffmpeg_filter_arg(path: Path) -> str:
    """ffmpeg 필터 인자용 경로 이스케이프 (\\ : , ' [ ])."""
    s = str(path)
    for ch in ("\\", ":", ",", "'", "[", "]"):
        s = s.replace(ch, "\\" + ch)
    return s
