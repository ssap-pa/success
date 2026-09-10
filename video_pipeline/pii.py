"""화면에 노출된 개인정보 탐지 (OCR + 정규식) 및 얼굴 탐지(옵션).

일정 간격으로 프레임을 샘플링해 EasyOCR로 글자를 읽고, 같은 줄의 글자를 이어붙여
전화번호·이메일·주민번호·카드/계좌번호·주소·차량번호·API 키 패턴을 찾는다.
탐지된 영역은 시간 범위를 가진 Region으로 반환되며 render 단계에서 모자이크된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from .utils import log

PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("phone", re.compile(r"(?<!\d)01[016789][\s\-.]?\d{3,4}[\s\-.]?\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)0(?:2|[3-6]\d|70|80)[\s\-.)]?\d{3,4}[\s\-.]?\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)1[5-9]\d{2}[\s\-.]?\d{4}(?!\d)")),                  # 1588-xxxx
    ("rrn", re.compile(r"(?<!\d)\d{6}[\s\-]?[1-4]\d{6}(?!\d)")),
    # OCR이 '.'을 '-'로 읽거나 빠뜨리는 경우가 많아 TLD 앞 점은 선택이지만, 끝은 실제 TLD여야 한다
    # (SNS 화면의 "threads.com @handle" 같은 조합이 이메일로 오인되는 것을 막음)
    ("email", re.compile(r"[A-Za-z0-9._%+\-]{2,}\s?@\s?[A-Za-z0-9\-]{1,}(?:[.\s\-][A-Za-z0-9\-]{1,})*?\.?"
                         r"(?:com|net|org|kr|co|io|ai|dev|me|edu|gov|info|biz|app|xyz|cloud|jp|us|uk)(?![A-Za-z])")),
    ("card", re.compile(r"(?<!\d)(?:\d{4}[\s\-]){3}\d{4}(?!\d)")),
    ("account", re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,6})?(?!\d)")),
    ("address", re.compile(r"[가-힣]{1,10}(?:시|도)\s*[가-힣]{1,10}(?:시|군|구)\s*[가-힣0-9]{1,15}(?:로|길|동)\s*\d+(?:-\d+)?")),
    ("address", re.compile(r"[가-힣]{1,15}(?:로|길)\s*\d{1,4}(?:-\d{1,4})?\s*(?:,\s*)?\d{1,4}동\s*\d{1,4}호")),
    ("plate", re.compile(r"(?<![가-힣\d])\d{2,3}[가-힣]\s?\d{4}(?!\d)")),
    ("secret", re.compile(r"(?:sk-(?:ant-)?[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}"
                          r"|xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{30,}"
                          r"|eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{10,})")),
    ("password", re.compile(r"(?:비밀번호|비번|password|pw|passwd)\s*[:：]\s*\S{4,}", re.I)),
]

_ACCOUNT_MIN_DIGITS = 10


@dataclass
class Region:
    start: float
    end: float
    x1: int
    y1: int
    x2: int
    y2: int
    kind: str
    text: str = ""
    hits: int = 1


def find_pii_in_text(text: str) -> list[tuple[str, int, int, str]]:
    """(kind, span_start, span_end, matched) 목록."""
    out = []
    for kind, pat in PII_PATTERNS:
        for m in pat.finditer(text):
            s = m.group(0)
            if kind == "account" and sum(c.isdigit() for c in s) < _ACCOUNT_MIN_DIGITS:
                continue
            if kind == "card" and sum(c.isdigit() for c in s) != 16:
                continue
            out.append((kind, m.start(), m.end(), s))
    return out


# ── OCR 결과를 줄 단위로 묶기 ─────────────────────────────────

def _bbox(pts) -> tuple[int, int, int, int]:
    xs = [int(p[0]) for p in pts]
    ys = [int(p[1]) for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _group_lines(items: list[tuple[tuple[int, int, int, int], str]]):
    """[(box, text)] → 같은 줄로 보이는 것들을 묶어 [[(box,text),...], ...] 반환 (x 오름차순)."""
    items = sorted(items, key=lambda it: ((it[0][1] + it[0][3]) / 2, it[0][0]))
    lines: list[list] = []
    for box, text in items:
        cy, h = (box[1] + box[3]) / 2, max(1, box[3] - box[1])
        placed = False
        for line in lines:
            lb = line[-1][0]
            lcy, lh = (lb[1] + lb[3]) / 2, max(1, lb[3] - lb[1])
            if abs(cy - lcy) < 0.6 * max(h, lh) and box[0] - lb[2] < 3.0 * max(h, lh):
                line.append((box, text))
                placed = True
                break
        if not placed:
            lines.append([(box, text)])
    for line in lines:
        line.sort(key=lambda it: it[0][0])
    return lines


def _scan_line(line, sep: str) -> list[tuple[str, str, tuple[int, int, int, int]]]:
    """줄의 텍스트를 sep로 이어붙여 검사하고, 매치에 걸린 박스들의 합집합을 돌려준다."""
    joined, spans = "", []
    for box, text in line:
        if joined:
            joined += sep
        s = len(joined)
        joined += text
        spans.append((s, len(joined), box))
    found = []
    for kind, ms, me, matched in find_pii_in_text(joined):
        boxes = [b for s, e, b in spans if s < me and e > ms]
        if not boxes:
            continue
        x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        found.append((kind, matched, (x1, y1, x2, y2)))
    return found


# ── 프레임 샘플링 & 탐지 ─────────────────────────────────────

def _iou(a: Region, b: Region) -> float:
    ix1, iy1, ix2, iy2 = max(a.x1, b.x1), max(a.y1, b.y1), min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    ua = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / max(1, ua)


def merge_regions(regions: list[Region], gap: float) -> list[Region]:
    """같은 위치(IoU>0.4)·같은 종류의 탐지가 시간상 가까우면 하나로 합친다 (OCR 깜빡임 방지)."""
    regions = sorted(regions, key=lambda r: r.start)
    merged: list[Region] = []
    for r in regions:
        target = None
        for m in reversed(merged):
            if m.end + gap < r.start:
                break
            iou = _iou(m, r)
            # 같은 종류면 위치가 대체로 겹칠 때, 다른 종류(예: phone/account 중복)면 거의 같은 박스일 때 병합
            if (m.kind == r.kind and iou > 0.4) or iou > 0.7:
                target = m
                break
        if target is None:
            merged.append(Region(**r.__dict__))
        else:
            target.start, target.end = min(target.start, r.start), max(target.end, r.end)
            target.x1, target.y1 = min(target.x1, r.x1), min(target.y1, r.y1)
            target.x2, target.y2 = max(target.x2, r.x2), max(target.y2, r.y2)
            target.hits += 1
            if len(r.text) > len(target.text):
                target.text = r.text
    return merged


def detect_pii_regions(video, cfg, progress_cb=None) -> list[Region]:
    import torch
    import easyocr

    use_gpu = torch.cuda.is_available()
    reader = easyocr.Reader(list(cfg.ocr_langs), gpu=use_gpu, verbose=False)
    log.info(f"  EasyOCR 준비 ({'GPU' if use_gpu else 'CPU'}), {cfg.ocr_interval}s 간격 샘플링")

    face_cascade = None
    if cfg.blur_faces:
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    step = max(1, int(round(fps * cfg.ocr_interval)))
    half = cfg.ocr_interval * 0.75

    # 속도 전략 1: OCR용으로 프레임을 줄인다 (1440p/4K → 1280 폭). 박스는 원래 좌표로 되돌린다.
    scale = 1.0
    if cfg.ocr_max_width and W > cfg.ocr_max_width:
        scale = cfg.ocr_max_width / W
        log.info(f"  OCR 해상도 {W}px → {cfg.ocr_max_width}px 로 축소")
    inv = 1.0 / scale

    # 속도 전략 2: 화면이 거의 안 바뀐 샘플은 직전 OCR 결과를 재사용한다.
    prev_small = None
    last_found: list = []
    last_ocr_t = -1e9
    ocr_runs = skipped = 0

    regions: list[Region] = []
    idx, sampled, last_pct = 0, 0, -1
    while True:
        if idx % step != 0:
            if not cap.grab():
                break
            idx += 1
            continue
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        sampled += 1

        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (192, 108), interpolation=cv2.INTER_AREA)
        changed = prev_small is None or float(cv2.absdiff(small, prev_small).mean()) > cfg.ocr_change_threshold
        prev_small = small

        if cfg.ocr_skip_static and not changed and (t - last_ocr_t) < cfg.ocr_force_interval:
            found = last_found
            skipped += 1
        else:
            img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame
            results = reader.readtext(img, detail=1, paragraph=False, batch_size=cfg.ocr_batch_size)
            items = []
            for b, txt, conf in results:
                if conf < cfg.ocr_min_conf or not txt.strip():
                    continue
                x1, y1, x2, y2 = _bbox(b)
                items.append(((int(x1 * inv), int(y1 * inv), int(x2 * inv), int(y2 * inv)), txt))
            found = []
            for line in _group_lines(items):
                found += _scan_line(line, " ")
                if len(line) > 1:
                    found += _scan_line(line, "")
            last_found, last_ocr_t = found, t
            ocr_runs += 1

        seen = set()
        for kind, matched, (x1, y1, x2, y2) in found:
            key = (kind, x1 // 8, y1 // 8, x2 // 8, y2 // 8)
            if key in seen:
                continue
            seen.add(key)
            regions.append(Region(max(0, t - half), t + half, x1, y1, x2, y2, kind, matched))

        if face_cascade is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if scale < 1:
                gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            for (x, y, w, h) in face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30)):
                regions.append(Region(max(0, t - half), t + half, int(x * inv), int(y * inv),
                                      int((x + w) * inv), int((y + h) * inv), "face"))

        if total and progress_cb:
            pct = int(idx * 100 / total)
            if pct // 10 != last_pct // 10:
                progress_cb(pct)
                last_pct = pct
        idx += 1
    cap.release()

    merged = merge_regions(regions, cfg.region_merge_gap)
    kinds = {}
    for r in merged:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    log.info(f"  샘플 {sampled}장 중 OCR {ocr_runs}장 (정적 화면 {skipped}장 재사용) → "
             f"개인정보 영역 {len(merged)}개 {kinds if kinds else ''}")
    return merged


def pixelate(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, block: int, margin: int) -> None:
    H, W = frame.shape[:2]
    x1, y1 = max(0, x1 - margin), max(0, y1 - margin)
    x2, y2 = min(W, x2 + margin), min(H, y2 + margin)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    roi = frame[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    small = cv2.resize(roi, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_LINEAR)
    frame[y1:y2, x1:x2] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
