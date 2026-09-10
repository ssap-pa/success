"""등장/퇴장 효과음을 numpy로 합성해 하나의 WAV 트랙으로 만든다 (외부 파일 불필요)."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    d = np.exp(-t / max(decay, 1e-4))
    return a * d


def pop(f0: float, f1: float, dur: float, gain: float = 1.0) -> np.ndarray:
    """짧은 주파수 스윕 '뽁' 소리."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = f0 + (f1 - f0) * (t / dur) ** 0.6
    phase = 2 * np.pi * np.cumsum(freq) / SR
    tone = np.sin(phase) + 0.25 * np.sin(2 * phase)
    return (tone * _env(n, 0.004, dur * 0.35) * gain).astype(np.float32)


def whoosh(dur: float, gain: float = 0.7, rising: bool = True) -> np.ndarray:
    """필터링한 노이즈 '슉' 소리 (큰 일러스트용)."""
    n = int(SR * dur)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n).astype(np.float32)
    # 간단한 1차 저역 필터를 시간에 따라 열거나 닫아 스윕 느낌을 낸다
    out = np.zeros(n, dtype=np.float32)
    y = 0.0
    for i in range(n):
        p = i / n
        cut = (0.02 + 0.25 * (p if rising else 1 - p))
        y += cut * (noise[i] - y)
        out[i] = y
    env = np.sin(np.pi * np.arange(n) / n) ** 1.5
    return out * env * gain * 3.0


SOUNDS = {
    "in_small": pop(420, 980, 0.11, 0.9),
    "out_small": pop(880, 380, 0.10, 0.7),
    "in_big": np.concatenate([whoosh(0.22, 0.5, rising=True), pop(300, 720, 0.13, 0.9)]),
    "out_big": np.concatenate([pop(700, 260, 0.12, 0.7), whoosh(0.16, 0.35, rising=False)]),
}


def build_track(events: list[tuple[float, str]], duration: float, path: Path, volume: float = 0.35) -> Path:
    """events: (시각, 사운드 이름). 전체 길이의 모노 WAV를 만든다."""
    n = int(SR * (duration + 1.0))
    track = np.zeros(n, dtype=np.float32)
    for t, name in events:
        snd = SOUNDS.get(name)
        if snd is None or t < 0:
            continue
        i = int(t * SR)
        j = min(n, i + len(snd))
        if j > i:
            track[i:j] += snd[: j - i]
    peak = float(np.abs(track).max()) if n else 0.0
    if peak > 0:
        track = track / max(peak, 1.0) * volume
    pcm = np.clip(track * 32767, -32768, 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path
