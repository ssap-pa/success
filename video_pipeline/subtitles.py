"""예능 자막(흑백요리사 풍) 번인용 ASS 파일 생성.

컷 편집본 대본(transcript_cut)을 받아 화면 하단 중앙에 굵은 고딕 + 두꺼운 검정 외곽선 +
그림자 자막을 만든다. 강조어(cfg.subtitle_emphasis)는 노란색으로 칠하고, 등장 시 살짝 커지는
팝 + 짧은 페이드를 넣는다. 렌더 단계에서 ffmpeg `ass` 필터(libass)로 프레임에 입힌다.
"""
from __future__ import annotations

import json
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


CAPTION_COLORS = {"yellow": "&H0000E5FF", "white": "&H00FFFFFF", "red": "&H003C3CFF", "green": "&H0060E070"}


def rewrite_tone(texts: list[str], tone: str, cfg) -> list[str]:
    """자막 말투 변환(OpenAI 텍스트 모델). 실패하면 원문을 그대로 돌려준다."""
    if not texts or not tone:
        return texts
    import openai

    guides = {
        "mz": ("요즘 한국 20대(MZ세대)가 친구와 카톡하듯 말하는 예능 자막 말투. "
               "줄임말·초성(ㅇㅈ, ㄹㅇ, ㄱㅊ)·'개-'·'찐'·'~함/~임' 체·'ㅋㅋ'·'ㅠㅠ'를 자연스럽게 섞되 한 줄에 하나 정도만. "
               "너무 억지스럽거나 유행 지난 표현(오지다, 지리다 등)은 피한다."),
    }
    guide = guides.get(tone, guides["mz"])
    system = (
        "너는 한국 예능 프로그램의 자막 작가다. 주어진 대사 목록을 아래 말투로 바꿔라.\n"
        f"말투: {guide}\n"
        "규칙: 의미와 화자의 의도는 그대로 유지한다. 새로운 정보나 감정을 지어내지 않는다. "
        "각 줄 길이는 원문의 1.3배를 넘지 않는다. 고유명사(곰돌이, 말차 등)는 유지하되 '아이스 아메리카노'는 '아아'로 줄여도 된다. "
        "존댓말을 쓰지 않는다. 한 줄 자막이므로 문장부호는 최소로 한다. 영어 단어는 한국어로 바꾼다.\n"
        '출력은 JSON 객체 {"lines": [...]} 하나만. 배열 길이는 입력과 정확히 같아야 한다.'
    )
    user = json.dumps({"lines": texts}, ensure_ascii=False)
    try:
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model=cfg.text_model, temperature=0.7,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        lines = json.loads(resp.choices[0].message.content or "{}").get("lines") or []
        if len(lines) != len(texts) or not all(isinstance(x, str) and x.strip() for x in lines):
            log.warning(f"  말투 변환 결과 줄 수 불일치({len(lines)}/{len(texts)}) → 원문 사용")
            return texts
        log.info(f"  자막 말투 변환({tone}, {cfg.text_model}): {len(lines)}줄")
        return [x.strip() for x in lines]
    except Exception as e:
        log.warning(f"  말투 변환 실패 → 원문 사용: {str(e)[:160]}")
        return texts


def tone_transcript(tr: Transcript, cfg, cache_path: Path) -> Transcript:
    """말투 변환을 적용한 대본 복사본. 같은 입력이면 cache_path 의 결과를 재사용한다."""
    import copy
    import hashlib

    texts = [s.text for s in tr.segments]
    key = hashlib.sha1(json.dumps([cfg.subtitle_tone, cfg.text_model, texts], ensure_ascii=False).encode()).hexdigest()
    lines = None
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                lines = cached["lines"]
                log.info(f"  캐시 사용: {cache_path.name} (말투 {cfg.subtitle_tone})")
        except Exception:
            lines = None
    if lines is None:
        lines = rewrite_tone(texts, cfg.subtitle_tone, cfg)
        cache_path.write_text(json.dumps({"key": key, "tone": cfg.subtitle_tone, "original": texts, "lines": lines},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    out = copy.deepcopy(tr)
    for s, line in zip(out.segments, lines):
        s.text = line
    return out


def build_ass(tr: Transcript, width: int, height: int, cfg, path: Path, captions: list | None = None) -> Path:
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
        # 임팩트 자막(예능 효과): 상단 중앙, 더 크고 두꺼운 외곽선
        f"Style: Impact,{font},{max(30, round(height * 0.09))},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(3, round(height * 0.009))},{round(height * 0.005)},8,{margin_h},{margin_h},"
        f"{round(height * 0.07)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    fx = "{\\fad(60,60)}"
    if st["pop"]:
        fx = "{\\fad(60,60)\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}"

    lines = []
    cues = build_cues(tr) if tr.segments else []
    for c in cues:
        text = _emphasize(_escape(_wrap(c.text)), emphasis, st["emphasis"])
        lines.append(f"Dialogue: 0,{_fmt(c.start)},{_fmt(c.end)},Default,,0,0,0,,{fx}{text}")
    n_cap = 0
    for cap in captions or []:
        if not cap.text:
            continue
        color = CAPTION_COLORS.get(cap.color, CAPTION_COLORS["yellow"])
        tag = ("{\\fad(40,140)\\c" + color + "&\\fscx55\\fscy55\\t(0,110,\\fscx110\\fscy110)"
               "\\t(110,200,\\fscx100\\fscy100)}")
        lines.append(f"Dialogue: 1,{_fmt(cap.start)},{_fmt(cap.end)},Impact,,0,0,0,,{tag}{_escape(cap.text)}")
        n_cap += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8-sig")
    log.info(f"  자막 {len(cues)}줄 ({cfg.subtitle_style}, {font} {size}px"
             + (f", 강조어 {len(emphasis)}개" if emphasis else "")
             + (f", 임팩트 자막 {n_cap}개" if n_cap else "") + f") → {path.name}")
    return path


def ffmpeg_filter_arg(path: Path) -> str:
    """ffmpeg 필터 인자용 경로 이스케이프 (\\ : , ' [ ])."""
    s = str(path)
    for ch in ("\\", ":", ",", "'", "[", "]"):
        s = s.replace(ch, "\\" + ch)
    return s
