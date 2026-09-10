"""faster-whisper로 단어 단위 타임스탬프가 있는 전사 결과를 만든다."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .utils import FFMPEG, log, run


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


def parse_fixes(spec: str) -> dict[str, str]:
    """'잘못=바름,잘못2=바름2' → {'잘못': '바름', ...}. 빈 항목·'=' 없는 항목은 무시."""
    out: dict[str, str] = {}
    for item in (spec or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    words: list[Word] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    language: str = "ko"

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(
            words=[Word(**w) for w in d.get("words", [])],
            segments=[Segment(**s) for s in d.get("segments", [])],
            language=d.get("language", "ko"),
        )

    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)

    def apply_fixes(self, fixes: dict[str, str]) -> "Transcript":
        """전사 오류 교정을 단어·문장에 모두 적용한다. 긴 키부터 치환하고,
        바로 뒤에 붙은 조사는 새 단어의 받침에 맞춰 바꾼다(뱃살은→곰돌이는, 뱃살이야→곰돌이야)."""
        if not fixes:
            return self
        for wrong, right in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
            for s in self.segments:
                s.text = fix_text(s.text, wrong, right)
            for w in self.words:
                w.text = fix_text(w.text, wrong, right)
        return self


# 받침 유무에 따라 짝이 바뀌는 조사: (받침 있을 때, 없을 때)
_PARTICLES = [("이야", "야"), ("이랑", "랑"), ("으로", "로"), ("은", "는"), ("을", "를"),
              ("이", "가"), ("과", "와"), ("아", "야")]


def _has_batchim(text: str) -> bool | None:
    """마지막 글자가 한글이면 받침 유무, 아니면 None."""
    if not text:
        return None
    code = ord(text[-1]) - 0xAC00
    if 0 <= code < 11172:
        return code % 28 != 0
    return None


def fix_text(text: str, wrong: str, right: str) -> str:
    """text 안의 wrong 을 right 로 바꾸고, 뒤따르는 조사를 right 의 받침에 맞춘다."""
    if wrong not in text:
        return text
    batchim = _has_batchim(right)
    alts = "|".join(re.escape(a) for pair in _PARTICLES for a in pair)
    pattern = re.compile(re.escape(wrong) + r"(" + alts + r")?(?=$|[\s,.!?])")

    def repl(m: re.Match) -> str:
        p = m.group(1) or ""
        if p and batchim is not None:
            for with_b, without_b in _PARTICLES:
                if p in (with_b, without_b):
                    p = with_b if batchim else without_b
                    break
        return right + p

    out = pattern.sub(repl, text)
    return out.replace(wrong, right)   # 조사 없이 단어 중간에 있는 경우


def extract_audio(video: Path, wav: Path) -> Path:
    """16kHz 모노 WAV 추출 (whisper, 무음 탐지 공용)."""
    wav.parent.mkdir(parents=True, exist_ok=True)
    run([FFMPEG, "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", wav])
    return wav


def _prepare_cuda_dlls():
    """Windows에서 ctranslate2가 cuDNN/cuBLAS DLL을 찾을 수 있게 torch·nvidia 패키지 경로를 등록."""
    if sys.platform != "win32":
        return
    candidates = []
    try:
        import torch  # noqa: F401  (torch/lib 에 cudnn64_9.dll, cublas64_12.dll 포함)
        candidates.append(Path(torch.__file__).parent / "lib")
    except Exception:
        pass
    for sp in sys.path:
        nv = Path(sp) / "nvidia"
        if nv.is_dir():
            for sub in nv.iterdir():
                b = sub / "bin"
                if b.is_dir():
                    candidates.append(b)
    for p in candidates:
        try:
            os.add_dll_directory(str(p))
            os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass


def _load_model(cfg):
    _prepare_cuda_dlls()
    from faster_whisper import WhisperModel

    device = cfg.whisper_device
    attempts = []
    if device in ("auto", "cuda"):
        attempts.append(("cuda", "float16"))
    if device in ("auto", "cpu"):
        attempts.append(("cpu", "int8"))
    last_err = None
    for dev, ctype in attempts:
        try:
            model = WhisperModel(cfg.whisper_model, device=dev, compute_type=ctype)
            log.info(f"  whisper 모델 로드: {cfg.whisper_model} ({dev}/{ctype})")
            return model
        except Exception as e:  # CUDA 라이브러리 미설치 등 → CPU로 폴백
            last_err = e
            log.warning(f"  whisper {dev} 로드 실패: {str(e).splitlines()[0][:120]}")
    raise RuntimeError(f"whisper 모델을 로드할 수 없습니다: {last_err}")


def transcribe(wav: Path, cfg) -> Transcript:
    model = _load_model(cfg)
    segments_iter, info = model.transcribe(
        str(wav),
        language=cfg.language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        beam_size=5,
        condition_on_previous_text=False,
    )
    tr = Transcript(language=info.language)
    for seg in segments_iter:
        tr.segments.append(Segment(float(seg.start), float(seg.end), seg.text.strip()))
        for w in seg.words or []:
            tr.words.append(Word(float(w.start), float(w.end), w.word.strip(), float(w.probability)))
    log.info(f"  전사 완료: 문장 {len(tr.segments)}개, 단어 {len(tr.words)}개")
    return tr
