"""영상 한 편에 대한 처리 흐름. 단계별로 실행할 수 있도록 Job 클래스로 구성.

단계(STEPS): transcribe → cut → plan → assets → pii → render
각 단계는 work/<영상>/ 에 산출물을 남기고, 다음 단계는 그것을 읽는다.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .config import PipelineConfig
from .cutter import CutPlan, Removal, TimeMap, plan_cuts, render_cut, words_inside_removals
from .planner import Plan, make_plan
from .transcribe import Transcript, extract_audio, transcribe
from .utils import Timer, fmt_clock, fmt_srt_time, load_json, log, probe, save_json

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".wmv"}
STEPS = ["transcribe", "cut", "plan", "assets", "pii", "render"]
PII_VERSION = 2          # 탐지 규칙(정규식·OCR 방식)이 바뀌면 올려서 옛 캐시를 무효화
STEP_LABELS = {
    "transcribe": "전사", "cut": "컷 편집", "plan": "시각 요소 기획", "assets": "이미지 생성",
    "pii": "개인정보 탐지", "render": "최종 렌더",
}


def safe_stem(p: Path) -> str:
    return re.sub(r"[^\w가-힣\-]+", "_", Path(p).stem).strip("_") or "video"


def _write_srt(tr: Transcript, path: Path) -> None:
    lines = []
    for i, s in enumerate(tr.segments, 1):
        lines += [str(i), f"{fmt_srt_time(s.start)} --> {fmt_srt_time(s.end)}", s.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _mask(text: str) -> str:
    t = (text or "").strip()
    return "*" * len(t) if len(t) <= 3 else t[:3] + "*" * max(3, len(t) - 3)


def _keeps_hash(keeps) -> str:
    return hashlib.md5(repr([(round(s, 3), round(e, 3)) for s, e in keeps]).encode()).hexdigest()[:10]


class Job:
    def __init__(self, src: Path, cfg: PipelineConfig):
        self.src = Path(src).resolve()
        if self.src.suffix.lower() not in VIDEO_EXTS:
            raise ValueError(f"지원하지 않는 확장자: {self.src.suffix}")
        self.cfg = cfg
        self.stem = safe_stem(self.src)
        self.work = cfg.work_dir / self.stem
        self.work.mkdir(parents=True, exist_ok=True)
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        self.out_video = cfg.out_dir / f"{self.stem}_edited.mp4"
        self.out_srt = cfg.out_dir / f"{self.stem}_edited.srt"
        self.out_report = cfg.out_dir / f"{self.stem}_report.md"
        self.p_wav = self.work / "audio.wav"
        self.p_tr = self.work / "transcript.json"
        self.p_cuts = self.work / "cuts.json"
        self.p_cut = self.work / "cut.mp4"
        self.p_cut_hash = self.work / "cut.hash"
        self.p_tr_cut = self.work / "transcript_cut.json"
        self.p_plan = self.work / "plan.json"
        self.p_pii = self.work / "pii.json"
        self.p_report = self.work / "report.json"
        self._info = None
        self.warnings: list[str] = []
        self._memo: dict = {}          # 한 실행 안에서 같은 단계를 두 번 계산하지 않도록

    # ── 상태 ─────────────────────────────────────────────
    @property
    def info(self) -> dict:
        if self._info is None:
            self._info = probe(self.src)
        return self._info

    def state(self) -> dict:
        plan = load_json(self.p_plan) if self.p_plan.exists() else None
        return {
            "stem": self.stem, "input": str(self.src),
            "transcribe": self.p_tr.exists(),
            "cut": self.p_cut.exists() and self.p_cuts.exists(),
            "plan": plan is not None,
            "assets": bool(plan) and all(s.get("image_path") for s in plan.get("scenes", []))
                      and all(o.get("image_path") for o in plan.get("overlays", []))
                      and bool(plan.get("scenes") or plan.get("overlays")),
            "pii": self.p_pii.exists(),
            "render": self.out_video.exists(),
            "output": str(self.out_video) if self.out_video.exists() else "",
            "report": str(self.out_report) if self.out_report.exists() else "",
        }

    # ── 1. 전사 ───────────────────────────────────────────
    def step_transcribe(self, force: bool = False) -> Transcript:
        if not self.info["has_audio"]:
            self.warnings.append("오디오가 없어 전사·컷 편집을 건너뜀")
            tr = Transcript()
            save_json(self.p_tr, tr)
            return tr
        with Timer("오디오 추출"):
            if force or not self.p_wav.exists():
                extract_audio(self.src, self.p_wav)
        with Timer("전사 (faster-whisper)"):
            if self.p_tr.exists() and not force:
                tr = Transcript.from_dict(load_json(self.p_tr))
                log.info(f"  캐시 사용: transcript.json (단어 {len(tr.words)}개)")
            else:
                tr = transcribe(self.p_wav, self.cfg)
                save_json(self.p_tr, tr)
        self._memo["transcript"] = tr
        return tr

    def ensure_transcript(self) -> Transcript:
        if "transcript" in self._memo:
            return self._memo["transcript"]
        if self.p_tr.exists() and self.cfg.reuse_cache:
            if self.info["has_audio"] and not self.p_wav.exists():
                extract_audio(self.src, self.p_wav)
            self._memo["transcript"] = Transcript.from_dict(load_json(self.p_tr))
            return self._memo["transcript"]
        return self.step_transcribe(force=not self.cfg.reuse_cache)

    # ── 2. 컷 편집 ────────────────────────────────────────
    def step_cut(self, force: bool = False) -> CutPlan:
        tr = self.ensure_transcript()
        duration = self.info["duration"]
        if self.info["has_audio"]:
            with Timer("컷 편집 계획 (무음·추임새·말더듬)"):
                cut_plan = plan_cuts(self.p_wav, tr, duration, self.cfg)
        else:
            cut_plan = CutPlan([], [(0.0, duration)], duration, duration)
        save_json(self.p_cuts, {"summary": cut_plan.summary(), "removals": cut_plan.removals,
                                "keeps": cut_plan.keeps})
        h = _keeps_hash(cut_plan.keeps)
        with Timer("컷 편집본 렌더"):
            if (self.p_cut.exists() and self.p_cut_hash.exists() and self.p_cut_hash.read_text() == h
                    and not force):
                log.info("  캐시 사용: cut.mp4")
            else:
                render_cut(self.src, cut_plan.keeps, self.p_cut, self.cfg, self.info["has_audio"], duration)
                self.p_cut_hash.write_text(h)
        tmap = TimeMap(cut_plan.keeps)
        tr_cut = tmap.remap_transcript(tr, words_inside_removals(tr, cut_plan.removals))
        save_json(self.p_tr_cut, tr_cut)
        if tr_cut.segments:
            _write_srt(tr_cut, self.out_srt)
        self._memo["cut"] = (cut_plan, tr_cut, h)
        return cut_plan

    def ensure_cut(self) -> tuple[CutPlan, Transcript, str]:
        if "cut" in self._memo:
            return self._memo["cut"]
        if (self.p_cut.exists() and self.p_cuts.exists() and self.p_tr_cut.exists()
                and self.p_cut_hash.exists() and self.cfg.reuse_cache):
            d = load_json(self.p_cuts)
            cp = CutPlan([Removal(**r) for r in d["removals"]], [tuple(k) for k in d["keeps"]],
                         d["summary"]["original_duration"], d["summary"]["new_duration"])
            self._memo["cut"] = (cp, Transcript.from_dict(load_json(self.p_tr_cut)), self.p_cut_hash.read_text())
            return self._memo["cut"]
        self.step_cut(force=not self.cfg.reuse_cache)
        return self._memo["cut"]

    # ── 3. 시각 요소 기획 ─────────────────────────────────
    def step_plan(self, force: bool = False) -> Plan:
        _, tr_cut, _ = self.ensure_cut()
        new_duration = self._cut_duration()
        with Timer("시각 요소 기획 (Claude)"):
            if self.p_plan.exists() and not force:
                plan = Plan.from_dict(load_json(self.p_plan))
                log.info(f"  캐시 사용: plan.json (일러스트 {len(plan.scenes)}개, 오버레이 {len(plan.overlays)}개)")
            else:
                plan = make_plan(tr_cut, new_duration, self.cfg)
                save_json(self.p_plan, plan)
        if plan.skipped_reason:
            self.warnings.append(f"시각 요소 기획 건너뜀: {plan.skipped_reason}")
        self._memo["plan"] = plan
        return plan

    def _cut_duration(self) -> float:
        return probe(self.p_cut)["duration"]

    def ensure_plan(self) -> Plan:
        if not self.cfg.illustrations and not self.cfg.overlays:
            return Plan(skipped_reason="설정에서 비활성화")
        if "plan" in self._memo:
            return self._memo["plan"]
        if self.p_plan.exists() and self.cfg.reuse_cache:
            self._memo["plan"] = Plan.from_dict(load_json(self.p_plan))
            return self._memo["plan"]
        return self.step_plan(force=not self.cfg.reuse_cache)

    # ── 4. 이미지 생성 ────────────────────────────────────
    def step_assets(self, force: bool = False) -> Plan:
        plan = self.ensure_plan()
        if not plan.scenes and not plan.overlays:
            log.info("  생성할 시각 요소가 없습니다.")
            return plan
        from .illustrator import choose_provider, generate_overlay_images, generate_scene_images
        old = self.cfg.reuse_cache
        if force:
            self.cfg.reuse_cache = False
        try:
            provider = choose_provider(self.cfg)
            with Timer("일러스트 생성"):
                generate_scene_images(plan.scenes if self.cfg.illustrations else [], self.cfg,
                                      self.work / "images", provider)
            with Timer("오버레이(로고·아이콘) 준비"):
                generate_overlay_images(plan.overlays if self.cfg.overlays else [], self.cfg,
                                        self.work / "images", provider)
        finally:
            self.cfg.reuse_cache = old
        save_json(self.p_plan, plan)
        self._memo["assets"] = plan
        return plan

    def ensure_assets(self) -> Plan:
        if "assets" in self._memo:
            return self._memo["assets"]
        plan = self.ensure_plan()
        need = [s for s in plan.scenes if not (s.image_path and Path(s.image_path).exists())] + \
               [o for o in plan.overlays if not (o.image_path and Path(o.image_path).exists())]
        if need or not self.cfg.reuse_cache:
            plan = self.step_assets(force=not self.cfg.reuse_cache)
        self._memo["assets"] = plan
        return plan

    # ── 5. 개인정보 탐지 ──────────────────────────────────
    def step_pii(self, force: bool = False) -> list:
        from .pii import Region, detect_pii_regions
        _, _, h = self.ensure_cut()
        with Timer("화면 개인정보 탐지 (OCR)"):
            cached = load_json(self.p_pii) if self.p_pii.exists() and not force else None
            if (cached and cached.get("cut_hash") == h and cached.get("blur_faces") == self.cfg.blur_faces
                    and cached.get("version") == PII_VERSION):
                regions = [Region(**r) for r in cached["regions"]]
                log.info(f"  캐시 사용: pii.json (영역 {len(regions)}개)")
            else:
                if cached and cached.get("version") != PII_VERSION:
                    log.info("  탐지 규칙이 바뀌어 개인정보를 다시 탐지합니다.")
                regions = detect_pii_regions(self.p_cut, self.cfg, lambda p: log.info(f"  OCR {p}%"))
                save_json(self.p_pii, {"version": PII_VERSION, "cut_hash": h, "blur_faces": self.cfg.blur_faces,
                                       "regions": regions})
        self._memo["pii"] = regions
        return regions

    def ensure_pii(self) -> list:
        if not self.cfg.mosaic:
            return []
        if "pii" in self._memo:
            return self._memo["pii"]
        return self.step_pii(force=not self.cfg.reuse_cache)

    # ── 6. 최종 렌더 + 리포트 ─────────────────────────────
    def step_render(self, force: bool = False) -> dict:
        t0 = time.time()
        cut_plan, _, _ = self.ensure_cut()
        plan = self.ensure_assets() if (self.cfg.illustrations or self.cfg.overlays) else Plan()
        regions = self.ensure_pii()
        scenes = plan.scenes if self.cfg.illustrations else []
        overlays = plan.overlays if self.cfg.overlays else []
        mute = [(p.start, p.end) for p in plan.spoken_pii] if self.cfg.mute_spoken_pii else []
        from .render import render_final
        with Timer("최종 렌더 (모자이크 + 일러스트 + 오버레이)"):
            render_final(self.p_cut, self.out_video, scenes, overlays, regions, mute, self.cfg)
        report = {
            "input": str(self.src), "output": str(self.out_video), "info": self.info,
            "warnings": list(dict.fromkeys(self.warnings)),
            "cuts": cut_plan.summary(), "new_duration": round(self._cut_duration(), 2),
            "srt": str(self.out_srt) if self.out_srt.exists() else "",
            "scenes": [{"start": s.start, "end": s.end, "concept": s.concept, "label": s.label,
                        "logo_hint": s.logo_hint, "image": Path(s.image_path).name if s.image_path else ""}
                       for s in scenes],
            "overlays": [{"start": o.start, "end": o.end, "kind": o.kind, "name": o.name,
                          "position": o.position, "image": Path(o.image_path).name if o.image_path else ""}
                         for o in overlays],
            "spoken_pii": [{"start": p.start, "end": p.end, "kind": p.kind, "text": _mask(p.text)}
                           for p in plan.spoken_pii],
            "pii_regions": [{"start": round(r.start, 2), "end": round(r.end, 2), "kind": r.kind,
                             "box": [r.x1, r.y1, r.x2, r.y2], "text": _mask(r.text), "hits": r.hits}
                            for r in regions],
            "elapsed_sec": round(time.time() - t0, 1),
        }
        save_json(self.p_report, report)
        self.out_report.write_text(_report_md(report, cut_plan, plan), encoding="utf-8")
        report["report"] = str(self.out_report)
        log.info(f"완료 → {self.out_video}")
        return report

    # ── 실행 ─────────────────────────────────────────────
    def run(self, steps: list[str] | None = None, force: bool = False):
        """steps가 전체면 최종 렌더가 앞 단계를 알아서 끌어온다(force면 전부 다시).
        일부 단계만 지정하면 그 단계들만 (force는 그 단계에만 적용, 앞 단계는 캐시 사용)."""
        steps = steps or STEPS
        bad = [s for s in steps if s not in STEPS]
        if bad:
            raise ValueError(f"알 수 없는 단계: {bad}")
        if list(steps) == STEPS:
            old = self.cfg.reuse_cache
            self.cfg.reuse_cache = old and not force
            try:
                return self.step_render(force=force)
            finally:
                self.cfg.reuse_cache = old
        result = None
        for s in steps:
            result = getattr(self, f"step_{s}")(force=force)
        return result


def process_video(src: Path, cfg: PipelineConfig) -> dict:
    t0 = time.time()
    job = Job(src, cfg)
    log.info(f"━━ {job.src.name}")
    i = job.info
    log.info(f"  {i['width']}x{i['height']} {i['fps']:.2f}fps {i['duration']:.1f}s "
             f"{'오디오 있음' if i['has_audio'] else '오디오 없음'}")
    report = job.run(STEPS, force=not cfg.reuse_cache)
    report["elapsed_sec"] = round(time.time() - t0, 1)
    log.info(f"  총 {report['elapsed_sec']}s")
    return report


def _report_md(rep: dict, cut_plan: CutPlan, plan: Plan) -> str:
    c = rep["cuts"]
    L = [f"# 편집 리포트: {Path(rep['input']).name}", "",
         f"- 결과 영상: `{rep['output']}`",
         f"- 길이: {fmt_clock(c['original_duration'])} → {fmt_clock(c['new_duration'])} "
         f"({c['removed_seconds']}초 단축, 컷 {c['cuts']}개)",
         f"- 처리 시간: {rep['elapsed_sec']}초", ""]
    if rep.get("warnings"):
        L += ["## 주의", *[f"- {w}" for w in rep["warnings"]], ""]

    L += ["## 컷 편집", "", "| 사유 | 개수 | 초 |", "|---|---|---|"]
    names = {"silence": "무음 축소", "filler": "추임새 제거", "stutter": "말더듬 제거"}
    for k, v in c["by_reason"].items():
        L.append(f"| {names.get(k, k)} | {v['count']} | {v['seconds']} |")
    L.append("")
    word_cuts = [r for r in cut_plan.removals if r.text]
    if word_cuts:
        L += ["제거된 단어 (원본 시각):", ""]
        L += [f"- {fmt_clock(r.start)} `{r.text}` ({r.reason})" for r in word_cuts[:40]]
        if len(word_cuts) > 40:
            L.append(f"- … 외 {len(word_cuts) - 40}개")
        L.append("")

    L += ["## 설명 일러스트 (중앙 카드)", ""]
    if rep["scenes"]:
        L += ["| 시각 | 개념 | 라벨 | 로고 | 이미지 |", "|---|---|---|---|---|"]
        L += [f"| {fmt_clock(s['start'])}–{fmt_clock(s['end'])} | {s['concept']} | {s['label']} "
              f"| {s['logo_hint']} | {s['image']} |" for s in rep["scenes"]]
    else:
        L.append("- 없음" + (f" ({plan.skipped_reason})" if plan.skipped_reason else ""))
    L += ["", "## 오버레이 (투명 로고·아이콘)", ""]
    if rep["overlays"]:
        L += ["| 시각 | 종류 | 이름 | 위치 | 파일 |", "|---|---|---|---|---|"]
        L += [f"| {fmt_clock(o['start'])}–{fmt_clock(o['end'])} | {o['kind']} | {o['name']} | {o['position']} "
              f"| {o['image']} |" for o in rep["overlays"]]
    else:
        L.append("- 없음")
    if plan.notes:
        L += ["", f"기획 메모: {plan.notes}"]
    L.append("")

    L += ["## 개인정보 (화면 → 모자이크 적용)", ""]
    if rep["pii_regions"]:
        L += ["| 시각 | 종류 | 내용(일부) | 위치 |", "|---|---|---|---|"]
        L += [f"| {fmt_clock(r['start'])}–{fmt_clock(r['end'])} | {r['kind']} | {r['text']} | {r['box']} |"
              for r in rep["pii_regions"]]
    else:
        L.append("- 탐지된 것 없음")
    L += ["", "## 개인정보 (음성)", ""]
    if rep["spoken_pii"]:
        L += ["| 시각 | 종류 | 내용(일부) |", "|---|---|---|"]
        L += [f"| {fmt_clock(p['start'])}–{fmt_clock(p['end'])} | {p['kind']} | {p['text']} |"
              for p in rep["spoken_pii"]]
        L += ["", "음성 개인정보는 기본적으로 음소거하지 않습니다. 옵션 '음성 개인정보 음소거'로 켤 수 있습니다."]
    else:
        L.append("- 탐지된 것 없음")
    L += ["", "---", "자동 편집 결과이므로 업로드 전 한 번 확인해 주세요. "
          "특히 일러스트·오버레이 위치와 모자이크 누락 여부를 점검하세요."]
    return "\n".join(L)
