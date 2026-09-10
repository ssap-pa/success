"""결과 파일을 Google Drive 폴더에 올린다 (서비스 계정, resumable 업로드).

설정:
  GDRIVE_SERVICE_ACCOUNT_JSON  서비스 계정 키 JSON 본문(한 줄), 그 base64 인코딩, 또는 JSON 파일 경로
  GDRIVE_FOLDER_ID             올릴 폴더 ID (또는 --drive-folder)

폴더는 서비스 계정 이메일(client_email)에 편집자로 공유돼 있어야 한다.
클라우드 세션은 파일 전송 한도(30MiB) 때문에 큰 영상을 직접 못 돌려주므로,
렌더가 끝나면 이 모듈로 Drive에 올려 PC에서 받게 한다.
"""
from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import re
from pathlib import Path

from .utils import log

SCOPES = ["https://www.googleapis.com/auth/drive"]
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
CHUNK = 16 * 1024 * 1024        # resumable 청크 (256KiB 배수)

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_\-]+)")


def folder_id_from(value: str | None) -> str | None:
    """폴더 ID 또는 Drive 폴더 URL → 폴더 ID."""
    if not value:
        return None
    m = _FOLDER_RE.search(value)
    return m.group(1) if m else value.strip()


def _load_service_account() -> dict:
    raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GDRIVE_SERVICE_ACCOUNT_JSON 이 비어 있습니다.")
    if raw.startswith("{"):
        return json.loads(raw)
    p = Path(raw)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    # 환경변수 칸이 여러 줄을 못 받는 경우를 위해 base64 한 줄(`base64 -w0 key.json`)도 허용
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        if decoded.lstrip().startswith("{"):
            return json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    raise RuntimeError(
        "GDRIVE_SERVICE_ACCOUNT_JSON 은 JSON 본문(한 줄), 그 base64, 또는 존재하는 파일 경로여야 합니다. "
        f"(현재 값: {len(raw)}자, JSON도 base64도 파일 경로도 아님)"
    )


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    info = _load_service_account()
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    log.info(f"  Drive 서비스 계정: {info.get('client_email', '?')}")
    return AuthorizedSession(creds)


def _err(resp) -> str:
    try:
        j = resp.json()
        return f"{resp.status_code} {j.get('error', {}).get('message', resp.text[:200])}"
    except Exception:
        return f"{resp.status_code} {resp.text[:200]}"


def upload_file(path: Path, folder_id: str, session=None, name: str | None = None) -> dict:
    """파일 하나를 resumable 업로드. 성공 시 {id, name, size, webViewLink} 반환."""
    path = Path(path)
    session = session or _session()
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    meta = {"name": name or path.name, "parents": [folder_id]}

    r = session.post(
        UPLOAD_URL, params={"uploadType": "resumable", "supportsAllDrives": "true",
                            "fields": "id,name,size,webViewLink"},
        headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)},
        json=meta, timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Drive 업로드 세션 생성 실패: {_err(r)}")
    return _send_chunks(path, r.headers["Location"], session)


def replace_file_content(path: Path, file_id: str, session=None) -> dict:
    """이미 있는 Drive 파일의 내용을 이 파일로 교체한다 (resumable, 소유자·ID 유지).

    서비스 계정은 저장 용량이 0이라 새 파일을 만들 수 없다(403 storageQuotaExceeded).
    대신 사용자가(또는 Drive MCP가) 만든 빈 파일의 내용을 서비스 계정이 채우면
    용량은 파일 소유자에게 계산되므로 영상도 올릴 수 있다.
    """
    path = Path(path)
    session = session or _session()
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    r = session.patch(
        f"{UPLOAD_URL}/{file_id}",
        params={"uploadType": "resumable", "supportsAllDrives": "true",
                "fields": "id,name,size,webViewLink"},
        headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)},
        json={}, timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Drive 내용 교체 세션 생성 실패 ({file_id}): {_err(r)}")
    return _send_chunks(path, r.headers["Location"], session)


def _send_chunks(path: Path, upload_uri: str, session) -> dict:
    size = path.stat().st_size
    sent = 0
    with path.open("rb") as f:
        while sent < size:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            r = session.put(
                upload_uri, data=chunk, timeout=600,
                headers={"Content-Length": str(len(chunk)),
                         "Content-Range": f"bytes {sent}-{end}/{size}"},
            )
            if r.status_code in (200, 201):
                log.info(f"  업로드 완료: {path.name} ({size / 1e6:.1f}MB)")
                return r.json()
            if r.status_code == 308:
                rng = r.headers.get("Range")          # "bytes=0-N"
                sent = int(rng.split("-")[1]) + 1 if rng else end + 1
                log.info(f"  업로드 {sent * 100 // size}% {path.name}")
                continue
            raise RuntimeError(f"Drive 업로드 실패 ({path.name}): {_err(r)}")
    raise RuntimeError(f"Drive 업로드가 끝나지 않았습니다: {path.name}")


def upload_outputs(paths: list[Path], folder_id: str) -> list[dict]:
    session = _session()
    # 폴더 접근 확인 (공유 안 됐으면 여기서 404)
    r = session.get(f"{FILES_URL}/{folder_id}",
                    params={"fields": "id,name,mimeType", "supportsAllDrives": "true"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Drive 폴더에 접근할 수 없습니다 ({folder_id}): {_err(r)}. "
                           "폴더를 서비스 계정 이메일에 편집자로 공유했는지 확인하세요.")
    log.info(f"  Drive 폴더: {r.json().get('name')} ({folder_id})")
    out = []
    for p in paths:
        if Path(p).is_file():
            out.append(upload_file(Path(p), folder_id, session))
    return out


def main(argv=None) -> int:
    """명령줄: 결과 파일을 Drive 폴더에 올리거나(--folder) 기존 파일 내용을 교체(--replace)한다."""
    import argparse

    p = argparse.ArgumentParser(prog="python -m video_pipeline.drive_upload",
                                description="Google Drive 업로드 (서비스 계정)")
    p.add_argument("paths", nargs="+", help="올릴 파일")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--folder", help="새 파일로 올릴 폴더 ID 또는 URL (서비스 계정에 저장 용량이 있어야 함)")
    g.add_argument("--replace", help="내용을 교체할 기존 파일 ID (파일 하나만, 소유자·ID 유지)")
    a = p.parse_args(argv)

    if a.replace:
        if len(a.paths) != 1:
            p.error("--replace 는 파일 하나만 받습니다.")
        info = replace_file_content(Path(a.paths[0]), a.replace)
        print(f"{info.get('name')} → {info.get('webViewLink')}")
        return 0
    for info in upload_outputs([Path(x) for x in a.paths], folder_id_from(a.folder)):
        print(f"{info.get('name')} → {info.get('webViewLink')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
