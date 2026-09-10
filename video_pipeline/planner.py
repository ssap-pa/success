"""Claude가 대본을 읽고 두 종류의 시각 요소를 기획한다.

- scenes   : 설명 일러스트(중앙 카드). 추상 개념·구조·비교처럼 그림이 있어야 이해되는 곳.
- overlays : 투명 배경 오브젝트(서비스 로고, 단일 아이콘)를 영상 위에 작게 얹는 것.
- spoken_pii: 말로 발화된 개인정보.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from .transcribe import Transcript
from .utils import fmt_clock, log

POSITIONS = ("top-right", "top-left", "bottom-right", "bottom-left", "right", "left", "center")


@dataclass
class Scene:
    start: float
    end: float
    concept: str
    image_prompt: str
    label: str = ""
    logo_hint: str = ""
    why: str = ""
    image_path: str = ""


@dataclass
class Overlay:
    start: float
    end: float
    kind: str                 # logo | icon
    name: str                 # 로고: 서비스명 / 아이콘: 짧은 이름
    icon_prompt: str = ""     # 아이콘일 때 영어 묘사
    position: str = "top-right"
    why: str = ""
    image_path: str = ""


@dataclass
class SpokenPII:
    start: float
    end: float
    kind: str
    text: str


@dataclass
class Plan:
    scenes: list[Scene] = field(default_factory=list)
    overlays: list[Overlay] = field(default_factory=list)
    spoken_pii: list[SpokenPII] = field(default_factory=list)
    notes: str = ""
    skipped_reason: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        from dataclasses import MISSING

        def build(klass, items):
            out = []
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                kw = {}
                for k, f in klass.__dataclass_fields__.items():
                    if k in it:
                        kw[k] = it[k]
                    elif f.default is not MISSING:
                        kw[k] = f.default
                    else:
                        kw[k] = 0.0 if f.type == "float" else ""
                out.append(klass(**kw))
            return out
        return cls(
            scenes=build(Scene, d.get("scenes")),
            overlays=build(Overlay, d.get("overlays")),
            spoken_pii=build(SpokenPII, d.get("spoken_pii")),
            notes=d.get("notes", ""),
            skipped_reason=d.get("skipped_reason", ""),
        )


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


PLAN_SCHEMA = _obj({
    "scenes": {"type": "array", "items": _obj({
        "start": {"type": "number"}, "end": {"type": "number"},
        "concept": {"type": "string"}, "image_prompt": {"type": "string"},
        "label": {"type": "string"}, "logo_hint": {"type": "string"}, "why": {"type": "string"},
    }, ["start", "end", "concept", "image_prompt", "label", "logo_hint", "why"])},
    "overlays": {"type": "array", "items": _obj({
        "start": {"type": "number"}, "end": {"type": "number"},
        "kind": {"type": "string", "enum": ["logo", "icon"]},
        "name": {"type": "string"}, "icon_prompt": {"type": "string"},
        "position": {"type": "string", "enum": list(POSITIONS)}, "why": {"type": "string"},
    }, ["start", "end", "kind", "name", "icon_prompt", "position", "why"])},
    "spoken_pii": {"type": "array", "items": _obj({
        "start": {"type": "number"}, "end": {"type": "number"},
        "kind": {"type": "string"}, "text": {"type": "string"},
    }, ["start", "end", "kind", "text"])},
    "notes": {"type": "string"},
}, ["scenes", "overlays", "spoken_pii", "notes"])

SYSTEM_PROMPT = """당신은 유튜브 교육/정보 영상의 편집 기획자입니다.
화자의 대본(타임스탬프 포함)을 읽고, 시청자의 이해를 돕는 시각 요소를 두 종류로 기획합니다.
또한 대본 안에서 말로 발화된 개인정보를 찾아냅니다.

1) scenes — 설명 일러스트 (화면 중앙에 카드로 뜨는 큰 그림)
- 그림이 있어야 이해가 확실히 쉬워지는 곳에만: 추상적 개념, 구조/흐름, 비교, 비유, 숫자 관계.
  인사·잡담·화자가 화면으로 직접 보여줄 법한 조작 설명에는 넣지 않습니다.
- 개수 상한을 지키고 최소 15초 간격을 둡니다. 개념을 말하기 시작하는 시점에 시작합니다.
- image_prompt는 영어로 그림 내용만 구체적으로 묘사합니다(스타일 지시는 따로 붙임).
  그림에 글자·숫자를 넣지 않는 전제이므로 은유·아이콘·도식으로 표현합니다.
- label은 화면 하단에 들어갈 한국어 한 줄, 8자 이내(없어도 됨). 글자는 최소한으로.
- logo_hint는 그 장면에서 언급된 서비스/브랜드명 하나(없으면 빈 문자열).

2) overlays — 투명 배경 오브젝트 (영상 위 한쪽 구석에 작게 얹는 로고/아이콘)
- kind="logo": 화자가 특정 서비스·브랜드·도구(예: 노션, 유튜브, 챗GPT, 엑셀)를 언급할 때. name에 서비스명.
- kind="icon": 짧고 구체적인 사물/개념을 아이콘 하나로 보강할 때(시계=시간, 자물쇠=보안, 돋보기=검색 등).
  name에 짧은 한국어 이름, icon_prompt에 영어로 아이콘 묘사(단일 오브젝트, 글자 없음).
- 장면(scenes)과 시간이 겹치지 않게 하고, 오버레이끼리도 겹치지 않게 합니다. 각 2.5~6초.
- position은 화자를 가리지 않을 자리를 고릅니다. 기본은 top-right.

3) spoken_pii — 전화번호, 이메일, 주소, 계좌번호, 주민등록번호, 카드번호, 비밀번호/API 키,
   실명+식별정보 조합 등 화자가 실제로 읊은 것만. 개념 설명("전화번호를 입력하세요")은 제외.
"""


def _format_transcript(tr: Transcript) -> str:
    return "\n".join(f"[{fmt_clock(s.start)}-{fmt_clock(s.end)} | {s.start:.1f}-{s.end:.1f}] {s.text}"
                     for s in tr.segments)


def make_plan(tr: Transcript, duration: float, cfg) -> Plan:
    if not tr.segments:
        return Plan(skipped_reason="전사 결과가 비어 있어 기획을 건너뜀")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log.warning("  ANTHROPIC_API_KEY가 설정되지 않아 설명 화면 기획을 건너뜁니다. (설정에서 키를 넣어 주세요)")
        return Plan(skipped_reason="ANTHROPIC_API_KEY 없음")

    max_scenes = max(1, math.ceil(duration / 60.0 * cfg.max_scenes_per_min))
    max_overlays = max(1, math.ceil(duration / 60.0 * cfg.max_overlays_per_min))
    user = (
        f"영상 길이: {duration:.1f}초. 설명 일러스트(scenes) 최대 {max_scenes}개, "
        f"각 {cfg.scene_min_dur:.0f}~{cfg.scene_max_dur:.0f}초. 오버레이(overlays) 최대 {max_overlays}개.\n"
        f"타임스탬프는 초 단위 실수(start/end)로 답하세요.\n\n대본:\n{_format_transcript(tr)}"
    )
    try:
        import anthropic
        client = anthropic.Anthropic()
        with client.messages.stream(
            model=cfg.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.AuthenticationError:
        log.warning("  ANTHROPIC_API_KEY가 잘못되어 기획을 건너뜁니다.")
        return Plan(skipped_reason="Anthropic API 인증 실패")
    except anthropic.APIStatusError as e:
        log.warning(f"  Claude API 오류({e.status_code}): {e.message[:200]}")
        return Plan(skipped_reason=f"Claude API 오류 {e.status_code}")
    except anthropic.APIConnectionError as e:
        log.warning(f"  Claude API 연결 실패: {e}")
        return Plan(skipped_reason="Claude API 연결 실패")
    except Exception as e:
        log.warning(f"  Claude 호출 실패: {str(e)[:200]}")
        return Plan(skipped_reason=f"Claude 호출 실패: {str(e)[:80]}")

    if resp.stop_reason == "refusal":
        return Plan(skipped_reason="모델이 요청을 거절함")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return Plan(skipped_reason="JSON 파싱 실패")

    plan = Plan.from_dict(data)
    plan.scenes = _sanitize_scenes(plan.scenes, duration, cfg, max_scenes)
    plan.overlays = _sanitize_overlays(plan.overlays, plan.scenes, duration, cfg, max_overlays)
    log.info(f"  일러스트 {len(plan.scenes)}개, 오버레이 {len(plan.overlays)}개, "
             f"발화 개인정보 {len(plan.spoken_pii)}건 기획")
    return plan


def _sanitize_scenes(scenes: list[Scene], duration: float, cfg, max_scenes: int) -> list[Scene]:
    out: list[Scene] = []
    for s in sorted(scenes, key=lambda x: x.start):
        start = max(0.0, min(float(s.start), duration - cfg.scene_min_dur))
        end = max(start + cfg.scene_min_dur, min(float(s.end), start + cfg.scene_max_dur, duration))
        if end - start < cfg.scene_min_dur * 0.8:
            continue
        if out and start < out[-1].end + 3.0:
            continue
        s.start, s.end = round(start, 2), round(end, 2)
        s.label = (s.label or "").strip()[:12]
        out.append(s)
        if len(out) >= max_scenes:
            break
    return out


def _sanitize_overlays(overlays: list[Overlay], scenes: list[Scene], duration: float, cfg,
                       max_overlays: int) -> list[Overlay]:
    out: list[Overlay] = []
    busy = [(s.start, s.end) for s in scenes]
    for o in sorted(overlays, key=lambda x: x.start):
        if o.kind not in ("logo", "icon") or not (o.name or o.icon_prompt):
            continue
        start = max(0.0, min(float(o.start), duration - 2.5))
        end = max(start + 2.5, min(float(o.end), start + 6.0, duration))
        if any(start < be and end > bs for bs, be in busy):
            continue
        if out and start < out[-1].end + 0.5:
            continue
        o.start, o.end = round(start, 2), round(end, 2)
        o.position = o.position if o.position in POSITIONS else "top-right"
        out.append(o)
        if len(out) >= max_overlays:
            break
    return out
