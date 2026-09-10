"""1차 컷 편집: 무음 구간 축소 + 추임새(필러워드)/말더듬 제거.

원본 타임라인에서 '제거 구간'을 계산하고, 그 여집합인 '유지 구간'을 ffmpeg select/aselect로 한 번에 이어붙인다 (trim+concat은 4K에서 메모리 폭주).
TimeMap은 원본 시간 → 편집본 시간 변환을 담당한다 (자막·설명 화면 위치 계산에 사용).
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path

from .transcribe import Segment, Transcript, Word
from .utils import FFMPEG, codec_args, log, run

_PUNCT = re.compile(r"[\s\.,…!?~\-—'\"“”‘’()\[\]:;]+")


def _norm(text: str) -> str:
    return _PUNCT.sub("", text).lower()


@dataclass
class Removal:
    start: float
    end: float
    reason: str      # silence | filler | stutter
    text: str = ""


# ── 탐지 ─────────────────────────────────────────────────────

def detect_silences(wav: Path, db: float, min_dur: float) -> list[tuple[float, float]]:
    res = run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", wav, "-af",
         f"silencedetect=noise={db}dB:d={min_dur}", "-f", "null", "-"],
        check=False,
    )
    starts, out = [], []
    for line in res.stderr.splitlines():
        m = re.search(r"silence_start:\s*([\d.\-]+)", line)
        if m:
            starts.append(float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.\-]+)", line)
        if m and starts:
            out.append((max(0.0, starts.pop(0)), float(m.group(1))))
    return out


def _effective_end(w: Word, silences: list[tuple[float, float]]) -> float:
    """whisper는 단어 뒤 정적을 단어 길이에 포함시키는 경우가 많다. 단어 안에서 시작하는 무음이 있으면 거기까지를 발화로 본다."""
    end = w.end
    for s, e in silences:
        if w.start < s < w.end:
            end = min(end, s)
        elif s > w.end:
            break
    return end


def find_filler_removals(tr: Transcript, cfg, silences: list[tuple[float, float]] | None = None) -> list[Removal]:
    fillers = {_norm(f) for f in cfg.filler_words}
    words = tr.words
    silences = sorted(silences or [])
    out: list[Removal] = []
    for i, w in enumerate(words):
        n = _norm(w.text)
        if not n:
            continue
        prev_end = words[i - 1].end if i > 0 else 0.0
        next_start = words[i + 1].start if i + 1 < len(words) else w.end + 1.0
        dur = _effective_end(w, silences) - w.start
        # 1) 추임새: 목록에 있고, 짧게 발화된 경우만
        if n in fillers and dur <= cfg.filler_max_dur:
            s = max(prev_end, w.start - cfg.edge_pad)
            e = min(next_start, w.end + cfg.edge_pad)
            if e - s > 0.05:
                out.append(Removal(s, e, "filler", w.text))
            continue
        # 2) 말더듬: 바로 다음 단어가 같은 단어이고 간격이 짧으면 앞쪽 것을 제거
        if cfg.remove_stutters and i + 1 < len(words):
            nxt = words[i + 1]
            if _norm(nxt.text) == n and len(n) <= 6 and (nxt.start - w.end) < 0.45:
                s = max(prev_end, w.start - cfg.edge_pad)
                e = min(nxt.start, w.end + cfg.edge_pad)
                if e - s > 0.05:
                    out.append(Removal(s, e, "stutter", w.text))
    return out


def silence_removals(silences: list[tuple[float, float]], cfg, duration: float) -> list[Removal]:
    out = []
    keep = cfg.keep_silence
    for s, e in silences:
        if e - s < cfg.min_silence:
            continue
        # 영상 맨 앞/뒤의 무음은 거의 다 잘라내고, 중간 무음은 keep_silence 만큼 남긴다
        if s <= 0.05:
            rs, re_ = s, max(s, e - keep * 0.5)
        elif e >= duration - 0.05:
            rs, re_ = min(e, s + keep * 0.5), e
        else:
            rs, re_ = s + keep / 2, e - keep / 2
        if re_ - rs >= cfg.min_cut:
            out.append(Removal(rs, re_, "silence"))
    return out


def avoid_words(removals: list[Removal], words: list[Word], pad: float) -> list[Removal]:
    """무음 제거 구간이 인식된 단어를 침범하지 않도록 잘라낸다 (경계 오차 보호)."""
    if not words:
        return removals
    spans = sorted((w.start - pad, w.end + pad) for w in words if w.text.strip())
    out: list[Removal] = []
    for r in removals:
        if r.reason != "silence":
            out.append(r)
            continue
        pieces = [(r.start, r.end)]
        for ws, we in spans:
            if we <= r.start or ws >= r.end:
                continue
            nxt = []
            for ps, pe in pieces:
                if we <= ps or ws >= pe:
                    nxt.append((ps, pe))
                else:
                    if ws > ps:
                        nxt.append((ps, ws))
                    if we < pe:
                        nxt.append((we, pe))
            pieces = nxt
        out += [Removal(ps, pe, r.reason) for ps, pe in pieces if pe - ps > 0.02]
    return out


def merge_removals(removals: list[Removal], duration: float, cfg) -> list[Removal]:
    rs = sorted((Removal(max(0, r.start), min(duration, r.end), r.reason, r.text) for r in removals),
                key=lambda r: r.start)
    merged: list[Removal] = []
    for r in rs:
        if r.end - r.start <= 0:
            continue
        if merged and r.start <= merged[-1].end + 0.01:
            m = merged[-1]
            m.end = max(m.end, r.end)
            if r.reason != m.reason:
                m.reason = f"{m.reason}+{r.reason}" if r.reason not in m.reason else m.reason
            if r.text and r.text not in m.text:
                m.text = (m.text + " " + r.text).strip()
        else:
            merged.append(Removal(r.start, r.end, r.reason, r.text))
    return [m for m in merged if m.end - m.start >= cfg.min_cut]


def keep_segments_from(removals: list[Removal], duration: float, cfg) -> list[tuple[float, float]]:
    keeps, cur = [], 0.0
    for r in removals:
        if r.start > cur:
            keeps.append((cur, r.start))
        cur = max(cur, r.end)
    if cur < duration:
        keeps.append((cur, duration))
    # 아주 짧은 유지 구간(깜빡임)은 버린다
    keeps = [(s, e) for s, e in keeps if e - s >= cfg.min_keep]
    return keeps or [(0.0, duration)]


# ── 시간 변환 ────────────────────────────────────────────────

class TimeMap:
    """원본 시간 → 편집본 시간."""

    def __init__(self, keeps: list[tuple[float, float]]):
        self.keeps = keeps
        self.starts = [s for s, _ in keeps]
        self.offsets = []
        acc = 0.0
        for s, e in keeps:
            self.offsets.append(acc)
            acc += e - s
        self.total = acc

    def to_new(self, t: float) -> float | None:
        """제거된 구간 안의 시각이면 None."""
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0:
            return 0.0
        s, e = self.keeps[i]
        if t > e + 1e-6:
            return None
        return self.offsets[i] + (t - s)

    def to_new_clamped(self, t: float) -> float:
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0:
            return 0.0
        s, e = self.keeps[i]
        return self.offsets[i] + min(max(t, s), e) - s

    def remap_transcript(self, tr: Transcript, removed_words: set[int] | None = None) -> Transcript:
        out = Transcript(language=tr.language)
        for idx, w in enumerate(tr.words):
            if removed_words and idx in removed_words:
                continue
            ns, ne = self.to_new(w.start), self.to_new(w.end)
            if ns is None and ne is None:
                continue
            ns = self.to_new_clamped(w.start) if ns is None else ns
            ne = self.to_new_clamped(w.end) if ne is None else ne
            if ne - ns < 0.02:
                continue
            out.words.append(Word(ns, ne, w.text, w.prob))
        for s in tr.segments:
            ns, ne = self.to_new_clamped(s.start), self.to_new_clamped(s.end)
            if ne - ns < 0.1:
                continue
            out.segments.append(Segment(ns, ne, s.text))
        return out


def words_inside_removals(tr: Transcript, removals: list[Removal]) -> set[int]:
    """제거 구간에 대부분 포함되는 단어 인덱스 (자막에서 빼기 위해)."""
    out = set()
    j = 0
    for i, w in enumerate(tr.words):
        mid = (w.start + w.end) / 2
        while j < len(removals) and removals[j].end < mid:
            j += 1
        if j < len(removals) and removals[j].start <= mid <= removals[j].end:
            out.add(i)
    return out


# ── 렌더 ─────────────────────────────────────────────────────

def render_cut(src: Path, keeps: list[tuple[float, float]], dst: Path, cfg, has_audio: bool,
               duration: float) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if len(keeps) == 1 and keeps[0][0] <= 0.01 and keeps[0][1] >= duration - 0.01:
        log.info("  잘라낼 구간이 없어 원본을 그대로 사용합니다.")
        run([FFMPEG, "-y", "-v", "error", "-i", src, "-c", "copy", dst])
        return dst

    # trim+concat 방식은 concat이 첫 구간을 내보내는 동안 뒤 구간의 프레임을 전부 메모리에 쌓아
    # 4K 영상에서 수십 GB를 먹고 OOM으로 죽는다. select/aselect는 한 스트림을 한 번만 훑으며
    # 유지 구간만 통과시키므로 메모리가 일정하다.
    expr = "+".join(f"between(t\\,{s:.4f}\\,{e:.4f})" for s, e in keeps)
    lines = [f"[0:v]select='{expr}',setpts=N/FRAME_RATE/TB[outv]"]
    if has_audio:
        lines.append(f";[0:a]aselect='{expr}',asetpts=N/SR/TB[outa]")
    script = dst.with_suffix(".filter.txt")
    script.write_text("\n".join(lines), encoding="utf-8")

    cmd = [FFMPEG, "-y", "-v", "error", "-i", src, "-filter_complex_script", script,
           "-map", "[outv]", *codec_args(cfg)]
    if has_audio:
        cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", dst]
    run(cmd)
    return dst


# ── 전체 흐름 ────────────────────────────────────────────────

@dataclass
class CutPlan:
    removals: list[Removal]
    keeps: list[tuple[float, float]]
    original_duration: float
    new_duration: float

    def summary(self) -> dict:
        by = {}
        for r in self.removals:
            key = r.reason.split("+")[0]
            by.setdefault(key, {"count": 0, "seconds": 0.0})
            by[key]["count"] += 1
            by[key]["seconds"] += r.end - r.start
        return {
            "original_duration": round(self.original_duration, 2),
            "new_duration": round(self.new_duration, 2),
            "removed_seconds": round(self.original_duration - self.new_duration, 2),
            "cuts": len(self.removals),
            "by_reason": {k: {"count": v["count"], "seconds": round(v["seconds"], 2)} for k, v in by.items()},
        }


def plan_cuts(wav: Path, tr: Transcript, duration: float, cfg) -> CutPlan:
    sil = detect_silences(wav, cfg.silence_db, cfg.min_silence)
    removals = avoid_words(silence_removals(sil, cfg, duration), tr.words, cfg.edge_pad)
    removals += find_filler_removals(tr, cfg, sil)
    merged = merge_removals(removals, duration, cfg)
    keeps = keep_segments_from(merged, duration, cfg)
    new_dur = sum(e - s for s, e in keeps)
    log.info(f"  무음 {len(sil)}곳, 컷 {len(merged)}개 → {duration:.1f}s → {new_dur:.1f}s")
    return CutPlan(merged, keeps, duration, new_dur)
