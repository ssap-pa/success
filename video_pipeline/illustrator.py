"""시각 요소 생성 + 합성.

- 일러스트(scenes): OpenAI gpt-image-1 (키 있을 때) / 없으면 PIL 플레이스홀더. 중앙 카드로 합성.
- 오버레이(overlays): 로고는 `로고/` 폴더에서 찾아 배경을 투명화, 아이콘은 투명 배경으로 생성.
  스티커처럼 흰 테두리 + 검정 외곽선을 둘러 영상 위에 바로 얹는다.
- 그림체: 굵은 검정 외곽선 + 플랫 컬러(머스터드 옐로·네이비·코랄·오렌지·러스트), 그림 안 글자 없음.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import random
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .planner import Overlay, Scene
from .utils import log

# ── 팔레트 (첨부 예시 그림체 기준) ─────────────────────────────
YELLOW = (247, 201, 72)
NAVY = (45, 56, 88)
CORAL = (246, 140, 131)
ORANGE = (240, 140, 40)
RUST = (150, 55, 25)
SLATE = (150, 160, 180)
INK = (20, 20, 24)
CREAM = (255, 252, 245)
PASTELS = [YELLOW, CORAL, ORANGE, SLATE, (255, 224, 130), (255, 180, 170)]

STYLE_SUFFIX = (
    " Illustration style: bold flat vector cartoon with thick black ink outlines around every shape, "
    "flat solid color fills with no gradients and no shading, limited palette of mustard yellow (#F7C948), "
    "deep navy blue-gray (#2D3858), coral salmon (#F68C83), bright orange (#F08C28) and rust red (#963719), "
    "clean geometric shapes with a slightly playful hand-drawn feel, isometric or slightly tilted top-down view, "
    "isolated on a fully transparent background, generous empty space, minimal detail, 16:9 composition. "
    "Absolutely no text, no letters, no numbers, no words, no captions, no logos, no watermark."
)
ICON_SUFFIX = (
    " Single flat vector cartoon icon of this object, thick black ink outline, flat solid fills using "
    "mustard yellow, navy blue-gray, coral and orange, no gradients, centered, sticker style, "
    "isolated on a fully transparent background, no text, no letters, no numbers, no shadow."
)

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\NotoSansKR-VF.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── 이미지 생성 제공자 ─────────────────────────────────────────

class OpenAIImageProvider:
    name = "openai"

    def __init__(self, cfg):
        from openai import OpenAI
        self.client = OpenAI()
        self.model = cfg.image_model
        self.quality = cfg.image_quality

    def generate(self, prompt: str, transparent: bool = False) -> Image.Image:
        # 아이콘은 정방형, 일러스트는 16:9. 둘 다 투명 배경으로 받아 카드/영상 위에 바로 얹는다.
        kwargs = dict(model=self.model, prompt=prompt, n=1,
                      size="1024x1024" if transparent else "1536x1024")
        if self.model.startswith("gpt-image"):
            kwargs["quality"] = self.quality
            kwargs["background"] = "transparent"
            kwargs["output_format"] = "png"
        else:
            kwargs["response_format"] = "b64_json"
        resp = self.client.images.generate(**kwargs)
        data = resp.data[0]
        if getattr(data, "b64_json", None):
            raw = base64.b64decode(data.b64_json)
        else:
            import urllib.request
            raw = urllib.request.urlopen(data.url, timeout=60).read()
        return Image.open(io.BytesIO(raw)).convert("RGBA")


class PlaceholderProvider:
    """API 키가 없을 때: 프롬프트별로 결정적인 손그림 도형을 그린다 (동일 팔레트)."""
    name = "placeholder"

    def __init__(self, cfg):
        pass

    def generate(self, prompt: str, transparent: bool = False) -> Image.Image:
        rnd = random.Random(int(hashlib.md5(prompt.encode()).hexdigest(), 16))
        if transparent:
            return self._icon(rnd)
        W, H = 1536, 1024
        img = Image.new("RGBA", (W, H), CREAM + (255,))
        d = ImageDraw.Draw(img)
        for _ in range(rnd.randint(3, 5)):
            cx, cy = rnd.randint(260, W - 260), rnd.randint(230, H - 230)
            rw, rh = rnd.randint(120, 260), rnd.randint(100, 220)
            color = rnd.choice(PASTELS)
            shape = rnd.choice(["ellipse", "rect", "blob"])
            box = [cx - rw, cy - rh, cx + rw, cy + rh]
            if shape == "ellipse":
                d.ellipse(box, fill=color, outline=INK, width=8)
            elif shape == "rect":
                d.rounded_rectangle(box, radius=36, fill=color, outline=INK, width=8)
            else:
                pts = _blob(cx, cy, rw, rh, rnd)
                d.polygon(pts, fill=color, outline=INK)
                d.line(pts + [pts[0]], fill=INK, width=8, joint="curve")
        for _ in range(rnd.randint(1, 2)):
            x0, y0 = rnd.randint(200, W - 400), rnd.randint(200, H - 200)
            _arrow(d, x0, y0, x0 + rnd.randint(150, 300), y0 + rnd.randint(-120, 120), rnd)
        for _ in range(rnd.randint(2, 5)):
            x, y, r = rnd.randint(80, W - 80), rnd.randint(80, H - 80), rnd.randint(8, 16)
            d.ellipse([x - r, y - r, x + r, y + r], fill=RUST, outline=INK, width=3)
        return img

    def _icon(self, rnd: random.Random) -> Image.Image:
        S = 512
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = rnd.choice([YELLOW, CORAL, ORANGE, SLATE])
        shape = rnd.choice(["circle", "rounded", "hex"])
        m = 60
        if shape == "circle":
            d.ellipse([m, m, S - m, S - m], fill=color, outline=INK, width=14)
        elif shape == "rounded":
            d.rounded_rectangle([m, m, S - m, S - m], radius=90, fill=color, outline=INK, width=14)
        else:
            pts = [(S / 2 + (S / 2 - m) * math.cos(math.pi / 3 * k - math.pi / 6),
                    S / 2 + (S / 2 - m) * math.sin(math.pi / 3 * k - math.pi / 6)) for k in range(6)]
            d.polygon(pts, fill=color, outline=INK)
            d.line(pts + [pts[0]], fill=INK, width=14, joint="curve")
        inner = rnd.choice(["dot", "bar", "check"])
        c = S / 2
        if inner == "dot":
            d.ellipse([c - 70, c - 70, c + 70, c + 70], fill=NAVY, outline=INK, width=10)
        elif inner == "bar":
            d.rounded_rectangle([c - 120, c - 30, c + 120, c + 30], radius=30, fill=NAVY, outline=INK, width=10)
        else:
            d.line([(c - 110, c), (c - 30, c + 80), (c + 120, c - 90)], fill=INK, width=44, joint="curve")
            d.line([(c - 110, c), (c - 30, c + 80), (c + 120, c - 90)], fill=NAVY, width=22, joint="curve")
        return img


def _blob(cx, cy, rw, rh, rnd):
    return [(cx + rw * rnd.uniform(0.75, 1.15) * math.cos(2 * math.pi * k / 20),
             cy + rh * rnd.uniform(0.75, 1.15) * math.sin(2 * math.pi * k / 20)) for k in range(20)]


def _arrow(d, x0, y0, x1, y1, rnd):
    mx, my = (x0 + x1) / 2 + rnd.randint(-40, 40), (y0 + y1) / 2 + rnd.randint(-60, 60)
    d.line([(x0, y0), (mx, my), (x1, y1)], fill=INK, width=9, joint="curve")
    ang = math.atan2(y1 - my, x1 - mx)
    for s in (-1, 1):
        d.line([(x1, y1), (x1 - 30 * math.cos(ang + s * 0.5), y1 - 30 * math.sin(ang + s * 0.5))],
               fill=INK, width=9)


def choose_provider(cfg):
    want = cfg.image_provider
    if want == "openai" or (want == "auto" and os.environ.get("OPENAI_API_KEY")):
        try:
            p = OpenAIImageProvider(cfg)
            log.info(f"  이미지 생성: OpenAI {cfg.image_model}")
            return p
        except Exception as e:
            log.warning(f"  OpenAI 이미지 초기화 실패({e}); 플레이스홀더로 대체")
    if want == "auto":
        log.warning("  OPENAI_API_KEY가 없어 플레이스홀더 그림을 사용합니다 (설정에서 키를 넣으면 실제 생성).")
    return PlaceholderProvider(cfg)


# ── 로고 검색 / 투명화 ─────────────────────────────────────────

class LogoIndex:
    EXTS = {".png", ".jpg", ".jpeg", ".webp"}

    def __init__(self, logo_dir: Path):
        self.items: list[tuple[set[str], Path]] = []
        if not logo_dir or not Path(logo_dir).is_dir():
            return
        for p in sorted(Path(logo_dir).iterdir()):
            if p.suffix.lower() not in self.EXTS:
                continue
            toks = {t.lower() for t in re.split(r"[_\s\-]+", p.stem) if t}
            toks -= {"로고", "logo", "아이콘", "icon"}
            toks = {t for t in toks if not re.fullmatch(r"\d+|백|흑|사각|클래스", t)}
            if toks:
                self.items.append((toks, p))

    def find(self, hint: str) -> Path | None:
        h = re.sub(r"[\s_\-]+", "", (hint or "").lower())
        if not h or not self.items:
            return None
        latin = re.sub(r"[^a-z0-9]", "", h)          # '챗gpt' → 'gpt' 처럼 영문 부분만 따로 비교
        best, best_score = None, 0
        for toks, p in self.items:
            for t in toks:
                tt = t.replace(" ", "")
                if tt == h:
                    score = 3
                elif len(tt) >= 3 and (tt in h or h in tt):
                    score = 2
                elif len(latin) >= 3 and latin in tt:
                    score = 1
                else:
                    continue
                score = score * 100 - len(p.stem)
                if score > best_score:
                    best, best_score = p, score
        return best


def ensure_transparent(img: Image.Image) -> Image.Image:
    """알파가 없거나 전부 불투명한 로고는 흰 배경을 투명하게 만들고 여백을 잘라낸다."""
    img = img.convert("RGBA")
    arr = np.asarray(img).astype(np.int16)
    if arr[:, :, 3].min() < 250:
        arr[:, :, 3] = np.where(arr[:, :, 3] < 40, 0, arr[:, :, 3])   # 거의 투명한 노이즈 제거
        out = Image.fromarray(arr.astype(np.uint8), "RGBA")
    else:
        rgb = arr[:, :, :3]
        dist = (255 - rgb).max(axis=2)                      # 흰색에서 얼마나 먼가
        alpha = np.clip((dist - 18) / 40.0, 0, 1) * 255
        arr[:, :, 3] = alpha.astype(np.int16)
        out = Image.fromarray(arr.astype(np.uint8), "RGBA")
    bbox = out.getchannel("A").getbbox()
    return out.crop(bbox) if bbox else out


# ── 합성: 카드 / 스티커 ────────────────────────────────────────

def _fit(img: Image.Image, w: int, h: int) -> Image.Image:
    r = min(w / img.width, h / img.height)
    return img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.LANCZOS)


def build_card(img: Image.Image | None, label: str, logo: Path | None, W: int, H: int,
               mode: str) -> tuple[np.ndarray, np.ndarray]:
    """반환: (BGR 카드, 0~1 알파). center: 화면 중앙 카드, pip: 우측 카드, cutaway: 전체 화면."""
    if mode == "pip":
        cw, ch = int(W * 0.42), int(H * 0.56)
    elif mode == "center":
        cw, ch = int(W * 0.60), int(H * 0.72)
    else:
        cw, ch = W, H
    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    outline = max(3, int(ch * 0.012))
    shadow = int(ch * 0.02) if mode != "cutaway" else 0
    radius = int(ch * 0.05) if mode != "cutaway" else 0

    # 코믹 스타일 카드: 오프셋 검정 그림자 + 크림 카드 + 굵은 외곽선
    if mode != "cutaway":
        d.rounded_rectangle([shadow, shadow, cw - 1, ch - 1], radius=radius, fill=INK + (255,))
        d.rounded_rectangle([0, 0, cw - 1 - shadow, ch - 1 - shadow], radius=radius,
                            fill=CREAM + (255,), outline=INK + (255,), width=outline)
        inner_w, inner_h = cw - shadow, ch - shadow
    else:
        d.rectangle([0, 0, cw, ch], fill=CREAM + (255,))
        inner_w, inner_h = cw, ch

    margin = int(inner_h * 0.06)
    label_h = int(inner_h * 0.17) if label else int(inner_h * 0.05)
    area_w, area_h = inner_w - 2 * margin, inner_h - margin - label_h - int(inner_h * 0.02)

    if img is not None:
        fitted = _fit(img.convert("RGBA"), area_w, area_h)
        card.alpha_composite(fitted, ((inner_w - fitted.width) // 2, margin + (area_h - fitted.height) // 2))

    if logo is not None:
        try:
            lg = ensure_transparent(Image.open(logo))
            lg = _fit(lg, int(inner_w * 0.15), int(inner_h * 0.10))
            pad = int(inner_h * 0.012)
            bx, by = inner_w - margin - lg.width - 2 * pad, margin // 2
            d.rounded_rectangle([bx, by, bx + lg.width + 2 * pad, by + lg.height + 2 * pad],
                                radius=pad * 2, fill=(255, 255, 255, 255), outline=INK + (255,), width=3)
            card.alpha_composite(lg, (bx + pad, by + pad))
        except Exception as e:
            log.debug(f"로고 합성 실패 {logo}: {e}")

    if label:
        fs = int(inner_h * 0.075)
        font = _font(fs)
        tw = d.textlength(label, font=font)
        pw, ph = int(tw + fs * 1.3), int(fs * 1.55)
        px, py = (inner_w - pw) // 2, inner_h - label_h + (label_h - ph) // 2
        d.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2, fill=YELLOW + (255,),
                            outline=INK + (255,), width=max(2, outline - 1))
        d.text((px + pw / 2, py + ph / 2), label, font=font, fill=INK + (255,), anchor="mm")

    alpha = np.asarray(card.getchannel("A"), dtype=np.float32) / 255.0
    rgb = np.asarray(card.convert("RGB"))
    return rgb[:, :, ::-1].copy(), alpha


def build_sticker(img: Image.Image, target_h: int, max_w: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """투명 PNG를 스티커처럼(흰 테두리 + 검정 외곽선) 만들어 (BGR, 알파)로 반환."""
    out = _sticker_image(img, target_h, max_w)
    alpha = np.asarray(out.getchannel("A"), dtype=np.float32) / 255.0
    rgb = np.asarray(out.convert("RGB"))
    return rgb[:, :, ::-1].copy(), alpha


def build_illustration_sprite(img: Image.Image | None, label: str, H: int, cfg) -> tuple[np.ndarray, np.ndarray]:
    """카드 없이 화면에 바로 얹는 일러스트 스티커 (+ 선택적 한 줄 라벨)."""
    target_h = int(H * cfg.scene_height)
    label_h = int(H * 0.09) if label else 0
    if img is None:
        img = PlaceholderProvider(cfg).generate("placeholder", transparent=True)
    st = _sticker_image(img, target_h - label_h, int(target_h * 1.7))
    if label:
        fs = int(H * 0.045)
        font = _font(fs)
        d0 = ImageDraw.Draw(st)
        tw = d0.textlength(label, font=font)
        pw, ph = int(tw + fs * 1.3), int(fs * 1.55)
        cw = max(st.width, pw + 8)
        canvas = Image.new("RGBA", (cw, st.height + label_h), (0, 0, 0, 0))
        canvas.alpha_composite(st, ((cw - st.width) // 2, 0))
        d = ImageDraw.Draw(canvas)
        px, py = (cw - pw) // 2, st.height + (label_h - ph) // 2
        d.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2, fill=YELLOW + (255,),
                            outline=INK + (255,), width=max(3, fs // 9))
        d.text((px + pw / 2, py + ph / 2), label, font=font, fill=INK + (255,), anchor="mm")
        st = canvas
    alpha = np.asarray(st.getchannel("A"), dtype=np.float32) / 255.0
    rgb = np.asarray(st.convert("RGB"))
    return rgb[:, :, ::-1].copy(), alpha


def _sticker_image(img: Image.Image, target_h: int, max_w: int | None = None) -> Image.Image:
    img = ensure_transparent(img)
    img = _fit(img, max_w or int(target_h * 1.6), target_h)
    white_w = min(14, max(3, target_h // 22))
    black_w = min(6, max(2, target_h // 40))
    pad = white_w + black_w + 2
    base = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    base.alpha_composite(img, (pad, pad))
    a = base.getchannel("A").point(lambda v: 255 if v > 110 else 0)   # 반투명 가장자리는 테두리에서 제외
    a_white = a.filter(ImageFilter.MaxFilter(2 * white_w + 1))
    a_black = a_white.filter(ImageFilter.MaxFilter(2 * black_w + 1))
    out = Image.new("RGBA", base.size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", base.size, INK + (255,)), mask=a_black)
    out.paste(Image.new("RGBA", base.size, (255, 255, 255, 255)), mask=a_white)
    out.alpha_composite(base)
    return out


# ── 전체 흐름 ─────────────────────────────────────────────────

def generate_scene_images(scenes: list[Scene], cfg, img_dir: Path, provider=None) -> None:
    if not scenes:
        return
    img_dir.mkdir(parents=True, exist_ok=True)
    provider = provider or choose_provider(cfg)
    for i, sc in enumerate(scenes):
        key = hashlib.md5((provider.name + sc.image_prompt).encode()).hexdigest()[:10]
        path = img_dir / f"scene{i + 1:02d}_{key}.png"
        if path.exists() and cfg.reuse_cache:
            sc.image_path = str(path)
            continue
        prompt = sc.image_prompt.strip() + STYLE_SUFFIX
        try:
            img = provider.generate(prompt)
        except Exception as e:
            log.warning(f"  장면 {i + 1} 이미지 생성 실패({e}); 플레이스홀더 사용")
            img = PlaceholderProvider(cfg).generate(prompt)
        img.save(path)
        sc.image_path = str(path)
        log.info(f"  일러스트 {i + 1}: {sc.concept} → {path.name}")


def generate_overlay_images(overlays: list[Overlay], cfg, img_dir: Path, provider=None) -> None:
    if not overlays:
        return
    img_dir.mkdir(parents=True, exist_ok=True)
    logos = LogoIndex(cfg.logo_dir)
    provider = provider or choose_provider(cfg)
    for i, ov in enumerate(overlays):
        if ov.kind == "logo":
            src = logos.find(ov.name)
            if src is not None:
                path = img_dir / f"overlay{i + 1:02d}_logo_{src.stem[:20]}.png"
                if not (path.exists() and cfg.reuse_cache):
                    ensure_transparent(Image.open(src)).save(path)
                ov.image_path = str(path)
                log.info(f"  오버레이 {i + 1}: 로고 '{ov.name}' ← {src.name}")
                continue
            log.info(f"  오버레이 {i + 1}: '{ov.name}' 로고 파일이 없어 아이콘으로 대체")
        prompt = (ov.icon_prompt or f"a simple symbolic icon representing {ov.name}").strip() + ICON_SUFFIX
        key = hashlib.md5((provider.name + prompt).encode()).hexdigest()[:10]
        path = img_dir / f"overlay{i + 1:02d}_icon_{key}.png"
        if not (path.exists() and cfg.reuse_cache):
            try:
                img = provider.generate(prompt, transparent=True)
            except Exception as e:
                log.warning(f"  오버레이 {i + 1} 아이콘 생성 실패({e}); 플레이스홀더 사용")
                img = PlaceholderProvider(cfg).generate(prompt, transparent=True)
            ensure_transparent(img).save(path)
        ov.image_path = str(path)
        log.info(f"  오버레이 {i + 1}: 아이콘 '{ov.name}' → {path.name}")
