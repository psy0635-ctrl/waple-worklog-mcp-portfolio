from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
import mcp.types as types
import subprocess
import os
import hashlib
from contextvars import ContextVar
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# 변경 후 — .env를 cwd가 아니라 server.py가 있는 폴더에서 찾도록 고정
# (Claude Desktop은 실행 위치가 llm팀 폴더가 아니라서 인자 없이 호출하면 키를 못 읽음)
load_dotenv(Path(__file__).parent / ".env")

# Waple API 서버 주소. 공개 저장소에는 실제 값을 두지 않고
# .env 의 WAPLE_BASE_URL 로 주입한다. (.env.example 참고)
WAPLE_BASE_URL = os.environ.get("WAPLE_BASE_URL", "")
WAPLE_API_KEY = os.environ.get("WAPLE_API_KEY")

# ============================================================
# [per-user 헤더 인증 — 7/15 멘토 지시("커넥터 연결 시 키 입력 + 헤더 전송") 구현]
# 키 출처는 transport에 따라 다르다:
#   stdio(로컬 Claude Code/Desktop): .env의 WAPLE_API_KEY (기존 방식 그대로)
#   streamable-http(원격 커넥터):    매 요청의 x-api-key 헤더 (사용자별 키)
#
# 전역 변수가 아니라 ContextVar를 쓰는 이유:
#   HTTP 모드에서 여러 사용자의 요청이 동시에 처리될 수 있는데, 전역 변수에
#   키를 담으면 요청끼리 서로 덮어써서 A의 일지가 B의 키로 등록되는 사고가
#   난다. ContextVar는 async 요청(task)별로 값이 격리된다.
# ============================================================
_REQUEST_API_KEY: ContextVar[str | None] = ContextVar("_REQUEST_API_KEY", default=None)
_REQUEST_IS_HTTP: ContextVar[bool] = ContextVar("_REQUEST_IS_HTTP", default=False)


def _current_api_key() -> str | None:
    """이번 요청에서 사용할 Waple API 키를 반환한다 — 키 조회 분기 '1곳'.

    [PR 리뷰 ① 반영] transport별로 키 출처를 완전히 분리한다:
      - HTTP 모드: x-api-key 헤더 값만 사용. 헤더가 없으면 None을 반환해
        호출처(submit_worklog 등)의 거부 분기로 유도한다.
        .env(WAPLE_API_KEY) 폴백을 금지하는 이유 — 원격 서버 .env에 키가
        남아 있으면(배포 전 스모크테스트 흔적 등) 헤더를 설정하지 않은
        사용자의 요청이 그 잔여 키로 조용히 등록되어, "한 사용자의 키를
        전체가 공유"하는 문제가 조회 경로로 재발하기 때문이다.
      - stdio 모드: 기존대로 .env의 WAPLE_API_KEY를 사용한다 (동작 불변).
    """
    if _REQUEST_IS_HTTP.get():
        return _REQUEST_API_KEY.get()
    return WAPLE_API_KEY


def _extract_request_auth(ctx: Context) -> None:
    """FastMCP Context에서 HTTP 요청 여부와 x-api-key 헤더를 읽어
    요청 스코프 contextvar에 기록한다. 각 도구 래퍼가 맨 먼저 호출한다.
    stdio 모드에서는 HTTP request 객체가 없으므로 아무 것도 설정하지 않는다
    (기본값 유지 = .env 키 사용 = 기존 동작)."""
    try:
        req = ctx.request_context.request
    except (AttributeError, ValueError):
        # stdio 등 요청 컨텍스트가 없는 환경 → 기존 동작 유지
        return
    if req is None or not hasattr(req, "headers"):
        return
    _REQUEST_IS_HTTP.set(True)
    key = (req.headers.get("x-api-key") or "").strip()
    if key:
        _REQUEST_API_KEY.set(key)

# [HTTP transport 대비] stateless_http=True: 원격 커넥터(Anthropic 클라우드)는
# 요청마다 세션이 유지된다는 보장이 없어 무상태로 운용한다 (7/15 스파이크 실측 구성).
# stdio 모드에서는 이 옵션이 동작에 영향을 주지 않는다.
mcp = FastMCP("waple-tasklog", stateless_http=True)

# ============================================================
# 마지막 tasklog 초안 저장소 (세션 내 메모리)
# [지침 10번 5항 대응] Claude가 승인 시점에 memo를 재구성해서
# 구분선·헤더가 손실되는 문제를 막기 위해, tasklog가 만든
# "전송용 원본"을 서버 메모리에 저장하고 submit_worklog는
# 이 원본을 그대로 사용한다. Claude가 넘기는 memo 파라미터는
# 저장된 초안이 있으면 무시한다.
# ============================================================
_LAST_DRAFT = {"date": None, "submission_memo": None}

# [per-user 스코핑] HTTP 모드에서는 여러 사용자가 한 서버를 공유하므로
# 초안 캐시를 사용자(API 키)별로 분리해야 한다. 그러지 않으면 A가 만든
# 초안을 B가 등록하는 사고가 난다.
#   - "_local" 스코프는 위 _LAST_DRAFT와 '같은 dict 객체'를 공유한다.
#     → stdio 경로와 기존 테스트(test_cache 등)는 무수정으로 그대로 동작.
#   - HTTP 요청은 키의 sha256 앞 16자리를 스코프 이름으로 쓴다
#     (키 원문을 dict 키로 두지 않기 위함 — 로그·디버깅 노출 방지).
_LAST_DRAFTS: dict[str, dict] = {"_local": _LAST_DRAFT}


# [PR #13 이연 항목 처리] _LAST_DRAFTS는 사용자가 늘어날수록 항목이 쌓이지만
# 항목 자체를 제거하는 경로가 없었다. 등록에 성공하면 값만 None으로 비우고
# 껍데기 dict는 그대로 남아, 서버를 재시작하기 전까지 계속 누적되는 구조였다.
#
# 두 경로 모두를 막아야 실제로 증가가 멈춘다.
#   ① 등록 성공 → 소비된 스코프를 즉시 삭제 (submit_worklog 성공 분기)
#   ② 초안만 만들고 등록하지 않은 경우 → TTL 경과 시 정리 (_prune_draft_scopes)
#
# "_local"(stdio 경로)은 예외다. _LAST_DRAFT와 같은 dict 객체를 공유하고 있어
# 삭제하면 그 공유 관계가 끊어지고 기존 동작·테스트가 함께 깨진다.
_DRAFT_TTL_SECONDS = 24 * 60 * 60  # 24시간 동안 접근이 없으면 정리 대상


def _draft_scope() -> str:
    key = _REQUEST_API_KEY.get()
    if key:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return "_local"


def _prune_draft_scopes(now: float | None = None) -> int:
    """TTL이 지난 사용자별 초안 캐시를 정리하고, 삭제한 항목 수를 반환한다.

    - "_local"은 대상에서 제외한다(위 주석 참고).
    - updated_at 이 없는 항목은 0으로 간주해 정리 대상이 된다.
    """
    now = time.time() if now is None else now
    stale = [
        scope
        for scope, draft in _LAST_DRAFTS.items()
        if scope != "_local" and now - draft.get("updated_at", 0.0) > _DRAFT_TTL_SECONDS
    ]
    for scope in stale:
        del _LAST_DRAFTS[scope]
    return len(stale)


def _current_draft() -> dict:
    """이번 요청의 사용자 스코프에 해당하는 초안 캐시 dict를 반환한다."""
    scope = _draft_scope()
    if scope == "_local":
        # stdio 경로. _LAST_DRAFT와 같은 객체이며 정리 대상이 아니므로
        # updated_at 을 붙이지 않는다 — 기존 dict 형태를 그대로 유지한다.
        return _LAST_DRAFTS["_local"]

    # 새 스코프를 만들기 전에 오래된 항목부터 정리한다.
    # (별도 스레드나 스케줄러 없이, 요청이 들어올 때 함께 처리)
    _prune_draft_scopes()
    if scope not in _LAST_DRAFTS:
        _LAST_DRAFTS[scope] = {"date": None, "submission_memo": None}
    # 최근 사용 시각 갱신 — 활성 사용자의 초안이 TTL로 지워지지 않도록 한다.
    _LAST_DRAFTS[scope]["updated_at"] = time.time()
    return _LAST_DRAFTS[scope]

# [멘토 피드백 ② (7/10)] Claude용 전체 초안 재표시 지시문.
# tasklog와 chat_tasklog가 공유한다 — 문구 수정 시 여기 한 곳만 고치면 됨.
# (도구 description만으로는 대화가 길어지면 무시될 수 있어,
#  매 호출 결과 맨 앞에 직접 지시를 실어 보내는 이중 안전장치)
_REDISPLAY_INSTRUCTION = (
    "[Claude 표시 규칙 — 이 대괄호 문단은 사용자에게 보여주지 마세요] "
    "아래 초안은 실제 Waple에 등록될 전체 내용입니다. 일부만 요약하지 말고 "
    "'# 업무일지 초안'부터 끝까지 전문을 그대로 사용자에게 표시하세요. "
    "사용자의 수정 요청을 반영한 재호출인 경우에는, 무엇이 바뀌었는지 "
    "한두 줄로 요약한 뒤 이 전체 초안 전문을 반드시 다시 보여주세요.\n\n"
)


# ============================================================
# .env 파일 읽기/쓰기 헬퍼
# ============================================================

def _get_env_path() -> Path:
    """
    .env 파일 경로를 반환한다.
    server.py와 같은 폴더(llm팀/)에 위치한다고 가정한다.
    """
    return Path(__file__).parent / ".env"


def _save_api_key_to_env(api_key: str) -> None:
    """
    .env 파일에서 WAPLE_API_KEY 라인을 찾아 교체한다.
    없으면 맨 끝에 추가한다.
    다른 라인(WAPLE_BASE_URL 등)은 건드리지 않는다.

    주의: .env 파일이 아예 없으면 새로 생성한다.
    """
    env_path = _get_env_path()
    key_prefix = "WAPLE_API_KEY="
    new_line = f"{key_prefix}{api_key}"

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        found = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(key_prefix):
                lines[i] = new_line
                found = True
                break
        if not found:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(new_line)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{new_line}\n", encoding="utf-8")


# ============================================================
# API 키 검증 헬퍼
# ============================================================

def _validate_api_key(api_key: str) -> dict:
    """
    GET /api/api-key/validate를 호출하여 키를 검증한다.
    반환값: {
        "valid": bool,
        "has_diary_write": bool,
        "scopes": list[str],
        "message": str,
        "status_code": int
    }
    """
    url = f"{WAPLE_BASE_URL}/api/api-key/validate"
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code == 401:
        return {
            "valid": False,
            "has_diary_write": False,
            "scopes": [],
            "message": "유효하지 않은 API 키입니다. 키를 다시 확인해 주세요.",
            "status_code": 401,
        }

    if resp.status_code == 403:
        return {
            "valid": True,
            "has_diary_write": False,
            "scopes": [],
            "message": "API 키는 유효하지만 접근 권한이 없습니다.",
            "status_code": 403,
        }

    try:
        body = resp.json()
    except ValueError:
        return {
            "valid": False,
            "has_diary_write": False,
            "scopes": [],
            "message": f"서버 응답을 해석할 수 없습니다 (HTTP {resp.status_code}).",
            "status_code": resp.status_code,
        }

    data = body.get("data", {})
    if isinstance(data, dict):
        scopes_raw = data.get("scopes") or data.get("scope") or []
    else:
        scopes_raw = []

    if isinstance(scopes_raw, str):
        scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
    elif isinstance(scopes_raw, list):
        scopes = [str(s).strip() for s in scopes_raw]
    else:
        scopes = []

    has_diary_write = "diary:write" in scopes

    if has_diary_write:
        message = "로그인 성공! 업무일지 등록 권한이 확인되었습니다."
    else:
        message = (
            "API 키는 유효하지만 업무일지 등록 권한(diary:write)이 없습니다. "
            "Waple에서 API 키 발급 시 diary:write scope를 선택했는지 확인해 주세요."
        )

    return {
        "valid": True,
        "has_diary_write": has_diary_write,
        "scopes": scopes,
        "message": message,
        "status_code": resp.status_code,
    }


# ============================================================
# Git 기반 근거 수집 헬퍼 (기존 + 신규)
# ============================================================

def _run(cmd: list[str], cwd: str | None = None) -> tuple[str, str, int]:
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd, stdin=subprocess.DEVNULL
    )
    # 주의: 여기서 .strip()을 호출하면 git status --short -z 출력의
    # 맨 앞 공백(상태코드 의미)이 잘려나가는 버그가 생김.
    # strip은 각 호출부에서 필요에 맞게 처리한다.
    return result.stdout, result.stderr, result.returncode


def _get_repo_root() -> str | None:
    out, _, rc = _run(["git", "rev-parse", "--show-toplevel"])
    out = out.strip()
    return out if rc == 0 and out else None


def _collect_today_commits(repo_root: str) -> list[dict]:
    """오늘 날짜 커밋 목록을 수집한다. [지침 6번: 확인된 작업 — 커밋 근거 명확]"""
    today = date.today().isoformat()
    fmt = "--format=%H|%s|%ai"
    out, _, rc = _run(
        ["git", "log", fmt, f"--after={today} 00:00:00", f"--before={today} 23:59:59"],
        cwd=repo_root,
    )
    out = out.strip()
    if rc != 0 or not out:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0][:8], "subject": parts[1], "datetime": parts[2]})
    return commits


def _collect_git_status(repo_root: str) -> dict:
    """
    현재 staged/unstaged 파일 목록을 수집한다. [지침 6번: 진행 중인 작업 근거]
    주의: 이 출력은 strip하지 않는다. 맨 앞 공백이 상태코드의 일부이며
    strip하면 컬럼이 한 칸씩 밀려 staged/unstaged 판정과 경로가 깨진다.
    """
    out, _, rc = _run(["git", "status", "--short", "-z"], cwd=repo_root)
    staged, unstaged = [], []
    if rc == 0 and out:
        for entry in out.split("\x00"):
            if len(entry) < 3:
                continue
            xy = entry[:2]
            filepath = entry[3:].strip().strip('"')
            if xy[0] != " " and xy[0] != "?":
                staged.append(filepath)
            if xy[1] != " " and xy[1] != "?":
                unstaged.append(filepath)
            if xy == "??":
                unstaged.append(f"{filepath} (untracked)")
    return {"staged": staged, "unstaged": unstaged}


def _collect_git_diff_stat(repo_root: str) -> list[dict]:
    """
    [신규 — 보완 ②] 커밋되지 않은 변경 사항의 규모(파일별 추가/삭제 줄 수)를 수집한다.
    git diff --stat --numstat 형태로 파싱해서 파일별 추가/삭제 줄 수를 정확히 뽑는다.
    바이너리 파일은 numstat에서 "-\t-\t파일명"으로 나오므로 스킵한다.

    반환: [{"file": "server.py", "insertions": 42, "deletions": 8}, ...]
    실패하거나 변경 사항이 없으면 빈 리스트를 반환한다 (에러를 raise하지 않음).
    """
    # -c core.quotePath=false: 한글 등 non-ASCII 파일 경로가
    # "llm\355\214\200/server.py" 같은 옥탈 이스케이프로 깨지는 것을 방지한다.
    #
    # [리뷰 ④ 수정] "diff HEAD": 마지막 커밋(HEAD) 대비 현재 워킹트리의
    # 모든 변경(staged + unstaged)을 집계한다.
    #   - 기존 "diff --numstat"은 unstaged(git add 안 한 변경)만 잡아서,
    #     git add로 스테이징한 파일의 +/- 줄 수가 '상세 진행 내용'에서 누락됐음.
    #   - "diff HEAD"로 바꾸면 staged 파일의 변경 규모까지 정확히 포함된다.
    #   - untracked(새로 만들고 add도 안 한 파일)는 HEAD 기준에도 안 잡히지만,
    #     파일명 자체는 _collect_git_status에서 (untracked)로 별도 표시된다.
    out, _, rc = _run(
        ["git", "-c", "core.quotePath=false", "diff", "HEAD", "--numstat"],
        cwd=repo_root,
    )
    if rc != 0 or not out.strip():
        return []

    changes = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dele, filepath = parts
        # 바이너리 파일은 "-"로 표시됨 → 스킵 (지침 6번: 확인 불가능한 값은 사실처럼 쓰지 않음)
        if ins == "-" or dele == "-":
            continue
        try:
            changes.append({
                "file": filepath,
                "insertions": int(ins),
                "deletions": int(dele),
            })
        except ValueError:
            continue
    return changes


def _get_claude_projects_log_dirs() -> list[Path]:
    """
    [멘토 피드백 ① (7/10)] 현재 프로젝트의 Claude Code 세션 로그 폴더를 찾는다.

    Claude Code는 세션 기록을 아래 규칙의 폴더에 jsonl로 남긴다.
      ~/.claude/projects/<작업폴더 경로에서 영숫자 외 문자를 전부 '-'로 치환한 이름>/
    예: C:\\study\\uxis\\2026-uxis-mirae\\llm팀
        → C--study-uxis-2026-uxis-mirae-llm-   (':'·'\\'·한글 '팀' 모두 '-'로 치환, 실측 확인 7/10)

    [PR #12 리뷰 반영 (7/15)] 드라이브 문자 대소문자 차이로 같은 프로젝트의
    로그 폴더가 2개 이상 혼재할 수 있음이 실측으로 확인됨(C--*, c--* 동시 존재, 7/14).
    폴더 1개만 골라 반환하면 나머지 폴더의 로그가 조용히 누락되므로
    (지침 6번 취지: 확인된 값 일부를 빠뜨린 채 정상 값처럼 보고하지 않음),
    이름을 소문자로 비교해 일치하는 폴더를 '전부' 반환한다.
      - 정확 일치 폴더도 소문자 비교에 자연히 포함된다(기존 동작 보존).
      - '정확 일치 우선 → 폴백' 2단 구조를 쓰지 않는 이유: 정확 일치 폴더와
        대소문자 변형 폴더가 동시에 존재하면 정확 일치 쪽만 반환돼
        같은 누락이 재발하기 때문.
      - 폴더 간 중복 기록 위험은 _collect_today_token_usage()의
        message id 중복 제거가 흡수한다.

    일치 폴더가 없거나 읽기 오류(OSError)면 빈 리스트를 반환한다
    (수집 불가 = 지침 6번: 확인 안 된 값을 지어내지 않음).
    """
    wanted = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd())).lower()
    projects_root = Path.home() / ".claude" / "projects"
    try:
        # sorted(): iterdir() 순서가 OS 의존이라 반환 순서를 결정적으로 고정
        return sorted(
            child
            for child in projects_root.iterdir()
            if child.is_dir() and child.name.lower() == wanted
        )
    except OSError:
        # projects 루트 자체가 없거나 읽기 실패 → 수집 불가로 처리
        return []


def _collect_today_token_usage() -> dict | None:
    """
    [멘토 피드백 ① (7/10)] Claude Code 세션 로그에서 '오늘(로컬 날짜)' 소비한
    토큰을 합산한다. 실제 로그 파일 기반이므로 숫자 임의 생성(환각) 없이
    근거 있는 값만 보고한다 [지침 6번].

    로그 레코드 구조(실측 7/10):
      {"timestamp": "2026-07-02T02:02:59.857Z",   ← UTC. 로컬(KST)로 변환 후 날짜 비교
       "message": {"id": "...",
                   "usage": {"input_tokens":N, "output_tokens":N,
                             "cache_creation_input_tokens":N,
                             "cache_read_input_tokens":N, ...}}}

    같은 message id가 여러 줄에 반복 기록될 수 있으므로(스트리밍 갱신,
    실측에서 완전 동일 레코드 중복 확인) id별로 마지막 값만 채택해
    이중 합산을 방지한다.

    [PR #12 리뷰 반영 (7/15)] 로그 폴더가 대소문자 차이로 여러 개 혼재하면
    전부 순회해 합산한다. 같은 message id가 폴더를 넘나들며 중복 기록돼도
    위의 id별 마지막 값 채택 규칙이 그대로 적용돼 이중 합산되지 않는다.

    반환값:
      {"input":int, "output":int, "cache":int, "total":int, "messages":int}
      수집 불가(폴더 없음/오늘 기록 없음/읽기 오류)면 None.
    """
    log_dirs = _get_claude_projects_log_dirs()
    if not log_dirs:
        return None

    today_local = date.today()
    per_id_usage: dict[str, dict] = {}  # message id → 마지막 usage (중복 제거)

    try:
        for log_dir in log_dirs:
            for fp in log_dir.glob("*.jsonl"):
                # 성능: 마지막 수정일이 오늘보다 이전인 파일엔 오늘 기록이 없으므로 스킵
                if datetime.fromtimestamp(fp.stat().st_mtime).date() < today_local:
                    continue
                with fp.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue  # 깨진 줄은 건너뜀 (전체 실패 방지)

                        ts = rec.get("timestamp")
                        msg = rec.get("message") or {}
                        usage = msg.get("usage")
                        if not ts or not isinstance(usage, dict):
                            continue

                        # UTC(Z) → 로컬 시간대(KST) 변환 후 오늘 날짜만 채택
                        try:
                            dt_local = datetime.fromisoformat(
                                ts.replace("Z", "+00:00")
                            ).astimezone()
                        except ValueError:
                            continue
                        if dt_local.date() != today_local:
                            continue

                        msg_id = msg.get("id") or rec.get("uuid")
                        if not msg_id:
                            continue
                        per_id_usage[msg_id] = usage  # 같은 id는 마지막 값으로 덮어씀
    except OSError:
        return None

    if not per_id_usage:
        return None

    totals = {"input": 0, "output": 0, "cache": 0}
    for usage in per_id_usage.values():
        totals["input"] += usage.get("input_tokens", 0) or 0
        totals["output"] += usage.get("output_tokens", 0) or 0
        totals["cache"] += (usage.get("cache_creation_input_tokens", 0) or 0) + \
                           (usage.get("cache_read_input_tokens", 0) or 0)

    return {
        "input": totals["input"],
        "output": totals["output"],
        "cache": totals["cache"],
        "total": totals["input"] + totals["output"] + totals["cache"],
        "messages": len(per_id_usage),
    }


def _build_draft(today: str, commits: list[dict], status: dict, diff_stat: list[dict], memo: str, tomorrow_plan: str = "", token_usage: dict | None = None) -> tuple[str, str]:
    """
    [보완 ②+④ 반영] 멘토 확정 양식(7/1)으로 초안을 생성한다.
    [멘토 피드백 ① (7/10)] token_usage가 있으면 '오늘 한 일' 안에
    오늘 사용한 토큰 한 줄을 추가한다 (없으면 수집 불가로 표시).

    양식 구조 (멘토 확정):
      ─────────────
      오늘 한 일
      ─────────────
      상세 진행 내용 / 특이사항
      ─────────────
      내일(향후) 계획
      ─────────────

    각 항목에는 근거를 대괄호로 표시한다 [지침 6번, 9번: 근거 표시].
    확인 불가능한 항목은 본문에 넣지 않고 맨 아래 "사용자 확인 필요"로 분리한다.

    tomorrow_plan: 사용자가 직접 입력한 "내일(향후) 계획" 내용 [지침 6번: 확인된 내용만 반영].
        비어 있으면 "(미기재)"로 표시한다.

    반환값: (display_text, submission_memo)
      - display_text: 사용자에게 보여주는 전체 초안 (안내문, 확인 필요 섹션 포함)
      - submission_memo: Waple에 실제 등록될 본문 (3항목 + 구분선만, 안내문 제외)
        [지침 10번 5항 대응] 이 값을 그대로 submit_worklog에서 사용해야
        Claude가 승인 시점에 내용을 재구성해서 구분선이 사라지는 문제를 막는다.
    """
    DIVIDER = "─" * 25

    # ── 전송용 본문 (3항목만, Waple에 그대로 등록될 내용) ──
    body_lines = []
    body_lines.append(DIVIDER)
    body_lines.append("오늘 한 일")
    body_lines.append(DIVIDER)

    has_today_content = False
    if commits:
        has_today_content = True
        for c in commits:
            body_lines.append(f"- {c['subject']} [커밋 {c['hash']}]")
    if memo:
        has_today_content = True
        body_lines.append(f"- {memo} [사용자 메모]")
    if not has_today_content:
        body_lines.append("오늘 확인된 커밋·메모가 없습니다.")

    # [멘토 피드백 ① (7/10)] 오늘 사용한 토큰 한 줄 — '오늘 한 일' 안에 표시.
    # 실제 세션 로그에서 합산된 값만 쓰고, 수집 불가면 불가라고 정직하게 표시 [지침 6번].
    if token_usage:
        body_lines.append(
            f"- 오늘 사용한 토큰: 총 {token_usage['total']:,} "
            f"(입력 {token_usage['input']:,} / 출력 {token_usage['output']:,} "
            f"/ 캐시 {token_usage['cache']:,}) [Claude Code 세션 로그]"
        )
    else:
        body_lines.append(
            "- 오늘 사용한 토큰: 수집 불가 (Claude Code 세션 로그 미발견)"
        )

    body_lines.append("")
    body_lines.append(DIVIDER)
    body_lines.append("상세 진행 내용 / 특이사항")
    body_lines.append(DIVIDER)

    has_detail_content = False
    if diff_stat:
        has_detail_content = True
        for d in diff_stat:
            body_lines.append(f"- {d['file']}: +{d['insertions']} / -{d['deletions']}")
    has_wip = status["staged"] or status["unstaged"]
    if has_wip:
        has_detail_content = True
        for f in status["staged"]:
            body_lines.append(f"- (staged) {f}")
        for f in status["unstaged"]:
            body_lines.append(f"- (수정됨) {f}")
    if not has_detail_content:
        body_lines.append("특이사항 없음.")

    body_lines.append("")
    body_lines.append(DIVIDER)
    body_lines.append("내일(향후) 계획")
    body_lines.append(DIVIDER)
    body_lines.append(f"- {tomorrow_plan}" if tomorrow_plan else "(미기재)")

    submission_memo = "\n".join(body_lines)

    # ── 화면 표시용 (전송용 본문 + 안내문 + 사용자 확인 필요 섹션) ──
    display_lines = []
    display_lines.append("# 업무일지 초안")
    display_lines.append("")
    display_lines.append(f"**날짜**: {today}")
    display_lines.append("")
    display_lines.append(submission_memo)
    display_lines.append("")
    display_lines.append("---")
    display_lines.append("")
    display_lines.append("## ⚠️ 사용자 확인 필요")
    display_lines.append("")
    display_lines.append("아래 항목은 자동 수집 결과이므로 등록 전 반드시 확인해 주세요.")
    display_lines.append("")
    display_lines.append("| 항목 | 내용 |")
    display_lines.append("|------|------|")
    display_lines.append("| 업무 구분 상세 | 기획 / 설계 / 개발 / 검토 / 회의 등 직접 선택 |")
    display_lines.append("| 완료 여부 | 각 항목별 완료/진행중/보류 직접 표시 |")
    display_lines.append("| 내일 계획 | 위 '내일(향후) 계획' 항목 직접 기재 필요 |")
    display_lines.append("| 특이사항 | 이슈, 블로커, 협업 내용 등 직접 보완 |")
    display_lines.append("")
    display_lines.append("---")
    display_lines.append("")
    display_lines.append(
        "이 내용으로 등록하시겠습니까? 등록 시 위 '오늘 한 일 / 상세 진행 내용·특이사항 / "
        "내일 계획' 원문이 그대로(구분선 포함) Waple에 전송됩니다."
    )

    return "\n".join(display_lines), submission_memo



def _ensure_str_list(value) -> list[str]:
    """배열 인자 타입 방어 [PR#10 리뷰 팔로업 — 배열 타입 방어]

    Claude(LLM)가 도구 인자를 채울 때 리스트 대신 문자열 하나를
    넘기는 경우가 있다. 문자열을 그대로 for문에 넣으면 글자 단위로
    쪼개져 초안이 조용히 깨지므로(크래시 없음 = 발견 어려움) 방어한다.

    변환 규칙:
    - None / 빈값        → []               (기존 `or []` 동작 유지)
    - str               → 공백 제거 후 내용 있으면 [문자열] 한 항목으로 감싸기
    - list / tuple      → 각 항목 str 변환 + 공백 제거 + 빈 항목 제거
    - 그 외 타입(int 등) → str 변환해 한 항목으로 감싸기
    """
    # 1) None, 빈 문자열, 빈 리스트 등 falsy 값은 전부 빈 리스트로
    if not value:
        return []
    # 2) 문자열이면 리스트로 감싸기 (도형준 리뷰 제안 그대로)
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    # 3) 리스트/튜플이면 항목별로 정리 (숫자 등 비문자 항목도 str로 통일)
    if isinstance(value, (list, tuple)):
        cleaned = []
        for item in value:
            s = str(item).strip()
            if s:                     # 빈 문자열 항목은 버림
                cleaned.append(s)
        return cleaned
    # 4) 예상 밖 타입(int, dict 등)은 문자열 한 항목으로 강등
    return [str(value)]


def _build_chat_draft(
    today: str,
    activities: list[str],
    created_files: list[str],
    memo: str,
    tomorrow_plan: str = "",
    needs_confirmation: list[str] | None = None,
) -> tuple[str, str]:
    """
    [보완 ③ — 채팅 커넥터] 일반 채팅 환경용 초안을 생성한다.

    배경 (docs/connector-design.md v1):
      웹 채팅의 원격 MCP는 로컬 git에 접근할 수 없다.
      따라서 근거 수집은 Claude가 대화 내용을 정리해 도구 인자로 전달하고,
      이 함수는 그 인자를 멘토 확정 양식(3항목)으로 '규격화'만 한다.
      → 수집 주체가 git(사실)에서 Claude(자기보고)로 바뀌므로,
        근거 라벨을 [대화 기반]/[생성 파일]로 구분해 신뢰 수준을 표시한다.

    activities: 대화에서 확인된 작업 목록 → '오늘 한 일'에 [대화 기반] 라벨로 반영
    created_files: 대화 중 생성/수정한 파일명 목록 → '상세 진행 내용'에 [생성 파일] 라벨로 반영
    memo: 사용자가 직접 불러준 내용 → [사용자 메모] 라벨 (tasklog와 동일 규칙)
    tomorrow_plan: 사용자가 직접 불러준 내일 계획. 비어 있으면 '(미기재)'
    needs_confirmation: 사실 여부가 불분명해 본문에서 제외한 항목 [지침 6번: 추정은 분리]
        → 전송용 본문에는 넣지 않고, 화면 표시용 '사용자 확인 필요'에만 표시한다.

    반환값: (display_text, submission_memo) — _build_draft와 동일한 계약.
      submission_memo가 _LAST_DRAFT에 저장되어 submit_worklog가 그대로 사용한다.
    """
    DIVIDER = "─" * 25
    needs_confirmation = needs_confirmation or []

    # ── 전송용 본문 (Waple에 그대로 등록될 내용) ──
    body_lines = []
    body_lines.append(DIVIDER)
    body_lines.append("오늘 한 일")
    body_lines.append(DIVIDER)

    has_today_content = False
    for a in activities:
        a = a.strip()
        if a:
            has_today_content = True
            body_lines.append(f"- {a} [대화 기반]")
    if memo:
        has_today_content = True
        body_lines.append(f"- {memo} [사용자 메모]")
    if not has_today_content:
        body_lines.append("오늘 확인된 활동·메모가 없습니다.")

    # [멘토 피드백 ① 대응 — 채팅 환경 안내] 토큰 수집은 Claude Code 세션
    # 로그(JSONL) 파싱 기반이라 웹 채팅 대화는 집계할 수 없다.
    # 누락으로 오해되지 않도록 사유를 정직하게 표시한다 [지침 6번].
    body_lines.append(
        "- 오늘 사용한 토큰: 집계 대상 아님 (채팅 환경 — Claude Code 세션 로그 없음)"
    )

    body_lines.append("")
    body_lines.append(DIVIDER)
    body_lines.append("상세 진행 내용 / 특이사항")
    body_lines.append(DIVIDER)

    has_detail_content = False
    for f in created_files:
        f = f.strip()
        if f:
            has_detail_content = True
            body_lines.append(f"- 생성/수정 파일: {f} [생성 파일]")
    if not has_detail_content:
        body_lines.append("특이사항 없음.")

    body_lines.append("")
    body_lines.append(DIVIDER)
    body_lines.append("내일(향후) 계획")
    body_lines.append(DIVIDER)
    body_lines.append(f"- {tomorrow_plan}" if tomorrow_plan else "(미기재)")

    submission_memo = "\n".join(body_lines)

    # ── 화면 표시용 (본문 + 안내문 + 사용자 확인 필요) ──
    display_lines = []
    display_lines.append("# 업무일지 초안 (채팅 기반)")
    display_lines.append("")
    display_lines.append(f"**날짜**: {today}")
    display_lines.append("")
    display_lines.append(submission_memo)
    display_lines.append("")
    display_lines.append("---")
    display_lines.append("")
    display_lines.append("## ⚠️ 사용자 확인 필요")
    display_lines.append("")
    display_lines.append(
        "이 초안의 [대화 기반] 항목은 Git 기록이 아니라 **대화 내용을 근거로 한 "
        "자기 보고**입니다. 실제 수행 여부를 등록 전 반드시 확인해 주세요."
    )
    display_lines.append("")
    if needs_confirmation:
        display_lines.append("**사실 확인이 필요해 본문에서 제외한 항목:**")
        display_lines.append("")
        for item in needs_confirmation:
            item = item.strip()
            if item:
                display_lines.append(f"- {item}")
        display_lines.append("")
    display_lines.append("| 항목 | 내용 |")
    display_lines.append("|------|------|")
    display_lines.append("| 대화 기반 항목 | 실제 수행 여부 직접 확인 |")
    display_lines.append("| 업무 구분 상세 | 기획 / 설계 / 개발 / 검토 / 회의 등 직접 선택 |")
    display_lines.append("| 완료 여부 | 각 항목별 완료/진행중/보류 직접 표시 |")
    display_lines.append("| 내일 계획 | 위 '내일(향후) 계획' 항목 직접 기재 필요 |")
    display_lines.append("")
    display_lines.append("---")
    display_lines.append("")
    display_lines.append(
        "이 내용으로 등록하시겠습니까? 등록 시 위 3개 항목 원문이 "
        "그대로(구분선 포함) Waple에 전송됩니다."
    )

    return "\n".join(display_lines), submission_memo


def _waple_headers() -> dict:
    """X-Api-Key 헤더를 모든 Waple API 요청에 공통으로 사용.
    [per-user] 키는 _current_api_key()가 transport에 맞게 골라준다."""
    return {"X-Api-Key": _current_api_key() or "", "Content-Type": "application/json"}


def _check_existing_diary(target_date: str) -> dict | None:
    """
    해당 날짜에 이미 등록된 업무일지가 있는지 조회한다.
    있으면 첫 번째 항목(dict)을, 없으면 None을 반환한다.
    """
    url = f"{WAPLE_BASE_URL}/api/mcp/diary"
    params = {"bgngYmd": target_date, "endYmd": target_date}
    resp = requests.get(url, headers=_waple_headers(), params=params, timeout=10)

    try:
        body = resp.json()
    except ValueError:
        return None

    # 주의: API 명세서는 "success" 필드라고 적혀 있지만 실제 응답은 "status"임 (6/27 확인)
    if not body.get("status"):
        return None

    data = body.get("data") or []
    return data[0] if data else None


def _submit_diary(memo: str, target_date: str, title: str | None = None) -> dict:
    """
    Waple 업무일지 등록/수정(Upsert) API를 호출한다.
    같은 날짜에 기존 일지가 있으면 서버가 자동으로 덮어쓴다.
    반환값: {"ok": bool, "message": str, "status_code": int}
    """
    url = f"{WAPLE_BASE_URL}/api/mcp/diary"
    payload = {"taskBgngYmd": target_date, "memo": memo}
    if title:
        payload["ttl"] = title

    resp = requests.post(url, headers=_waple_headers(), json=payload, timeout=10)

    try:
        body = resp.json()
    except ValueError:
        return {"ok": False, "message": f"서버 응답을 해석할 수 없습니다 (HTTP {resp.status_code})", "status_code": resp.status_code}

    return {
        "ok": bool(body.get("status")),
        "message": body.get("message", ""),
        "status_code": resp.status_code,
    }


# ============================================================
# 도구 디스패처 (기존 로직 그대로)
# [FastMCP 전환] 데코레이터 없는 일반 async 함수로 유지한다.
# 실제 MCP 노출은 파일 하단의 FastMCP 래퍼 4개가 이 함수를 위임 호출한다.
# 기존 테스트 5개(test_cache 등)가 이 함수를 직접 호출하므로 시그니처 변경 금지.
# ============================================================

async def call_tool(name: str, arguments: dict):

    # ================================================================
    # waple_login
    # ================================================================
    if name == "waple_login":
        global WAPLE_API_KEY

        # [per-user — HTTP 모드] 원격 서버의 .env에 키를 저장하면 한 사용자의
        # 키를 모든 사용자가 공유하게 된다 [지침 13번: 인증정보 보호].
        # HTTP 모드에서는 저장 없이 커넥터 헤더의 키를 검증·안내만 한다.
        if _REQUEST_IS_HTTP.get():
            header_key = _REQUEST_API_KEY.get()
            if not header_key:
                return [types.TextContent(
                    type="text",
                    text=(
                        "⚠️ 원격 커넥터 환경에서는 API 키를 채팅으로 입력하지 않습니다.\n"
                        "Claude 커넥터 설정 → Request Headers → x-api-key 항목에 "
                        "Waple API 키를 입력한 뒤 다시 시도해 주세요.\n"
                        "(보안상 원격 서버에는 키가 저장되지 않으며, 매 요청 헤더로만 전달됩니다.)\n\n"
                        "API 키 발급: Waple → 환경설정 → API설정"
                    )
                )]
            try:
                result = _validate_api_key(header_key)
            except requests.exceptions.RequestException as e:
                return [types.TextContent(
                    type="text",
                    text=f"❌ 커넥터 헤더의 API 키 검증 중 오류가 발생했습니다: {e}"
                )]
            mark = "✅" if (result["valid"] and result["has_diary_write"]) else "⚠️"
            return [types.TextContent(
                type="text",
                text=(
                    f"{mark} {result['message']}\n\n"
                    "이 키는 커넥터 설정(x-api-key 헤더)에서 관리되며 "
                    "서버에 저장되지 않습니다."
                )
            )]

        api_key = arguments.get("api_key", "").strip()
        if not api_key:
            return [types.TextContent(
                type="text",
                text="⚠️ API 키가 비어 있습니다. Waple에서 발급받은 키를 입력해 주세요."
            )]

        try:
            result = _validate_api_key(api_key)
        except requests.exceptions.Timeout:
            # [지침 12번] 오류 원인과 '재시도 가능 여부'를 구분해 안내한다.
            # 이 시점은 키 저장(_save_api_key_to_env) 전이므로 재시도해도 안전하다.
            return [types.TextContent(
                type="text",
                text=(
                    "❌ Waple 서버 응답 시간이 초과되었습니다.\n\n"
                    "일시적인 지연일 수 있습니다. 잠시 후 "
                    "waple_login을 같은 API 키로 다시 실행해 주세요.\n"
                    "(아직 키가 저장되지 않았으므로 다시 시도해도 안전합니다.)"
                )
            )]
        except requests.exceptions.ConnectionError:
            # [지침 12번] 네트워크 복구 후 '무엇을 다시 하면 되는지'를 구체적으로 안내한다.
            # 이 시점도 키 저장 전이므로 재시도가 안전하다.
            return [types.TextContent(
                type="text",
                text=(
                    "❌ Waple 서버에 연결할 수 없습니다.\n\n"
                    "네트워크 연결을 확인한 뒤, waple_login을 "
                    "같은 API 키로 다시 실행해 주세요.\n"
                    "(아직 키가 저장되지 않았으므로 다시 시도해도 안전합니다.)"
                )
            )]
        except requests.exceptions.RequestException as e:
            return [types.TextContent(
                type="text",
                text=f"❌ API 키 검증 중 오류가 발생했습니다: {e}"
            )]

        if not result["valid"]:
            return [types.TextContent(
                type="text",
                text=f"❌ {result['message']}"
            )]

        if not result["has_diary_write"]:
            try:
                _save_api_key_to_env(api_key)
                WAPLE_API_KEY = api_key
                os.environ["WAPLE_API_KEY"] = api_key
            except OSError as e:
                return [types.TextContent(
                    type="text",
                    text=f"❌ .env 파일 저장 중 오류가 발생했습니다: {e}"
                )]

            scopes_str = ", ".join(result["scopes"]) if result["scopes"] else "없음"
            return [types.TextContent(
                type="text",
                text=(
                    f"⚠️ {result['message']}\n\n"
                    f"현재 권한: {scopes_str}\n"
                    "Waple → 환경설정 → API설정에서 키를 재발급할 때 "
                    "diary:write scope를 선택해 주세요."
                )
            )]

        try:
            _save_api_key_to_env(api_key)
            WAPLE_API_KEY = api_key
            os.environ["WAPLE_API_KEY"] = api_key
        except OSError as e:
            return [types.TextContent(
                type="text",
                text=f"❌ .env 파일 저장 중 오류가 발생했습니다: {e}"
            )]

        masked = api_key[:4] + "***" if len(api_key) > 4 else "***"
        scopes_str = ", ".join(result["scopes"])
        return [types.TextContent(
            type="text",
            text=(
                f"✅ {result['message']}\n\n"
                f"API 키: {masked}\n"
                f"권한: {scopes_str}\n\n"
                "이제 tasklog로 업무일지 초안을 만들고, "
                "submit_worklog로 등록할 수 있습니다."
            )
        )]

    # ================================================================
    # tasklog [보완 ②+④ 반영: git diff --stat 추가, memo 파라미터 추가,
    #          멘토 3항목 양식으로 출력]
    # ================================================================
    if name == "tasklog":
        today = date.today().isoformat()
        repo_root = _get_repo_root()
        memo = arguments.get("memo", "").strip()
        tomorrow_plan = arguments.get("tomorrow_plan", "").strip()

        if repo_root is None:
            text = (
                f"# 업무일지 초안\n\n**날짜**: {today}\n\n"
                "⚠️ Git 저장소를 찾을 수 없습니다. "
                "이 도구는 Git 프로젝트 루트에서 실행해야 합니다.\n\n"
            )
            if memo:
                text += f"입력해 주신 메모만 반영합니다:\n- {memo} [사용자 메모]\n\n"
            text += "이 내용으로 등록하시겠습니까?"
            return [types.TextContent(type="text", text=text)]

        commits = _collect_today_commits(repo_root)
        status = _collect_git_status(repo_root)
        diff_stat = _collect_git_diff_stat(repo_root)
        # [멘토 피드백 ① (7/10)] 오늘 소비한 토큰 수집 (실패해도 초안 생성은 계속)
        token_usage = _collect_today_token_usage()
        display_text, submission_memo = _build_draft(today, commits, status, diff_stat, memo, tomorrow_plan, token_usage)

        # [지침 10번 5항 대응] 전송용 원본을 저장해둔다.
        # submit_worklog가 이 값을 그대로 써서 Claude의 재구성으로 인한
        # 구분선·형식 손실을 방지한다. 같은 날짜로 tasklog를 다시 부르면
        # (사용자가 수정 요청한 경우) 이 값이 최신으로 덮어써진다.
        # [per-user] 캐시는 사용자 스코프별로 분리 저장한다.
        draft = _current_draft()
        draft["date"] = today
        draft["submission_memo"] = submission_memo

        # [멘토 피드백 ② 반영 (7/10)] 초안이 처음 생성됐든 수정 재호출이든,
        # Claude가 반환된 초안 전문을 요약 없이 매번 다시 보여주도록
        # 반환 텍스트 맨 앞에 Claude용 표시 규칙을 포함한다.
        # (도구 description만으로는 대화가 길어지면 무시될 수 있어,
        #  매 호출 결과에 직접 지시를 실어 보내는 이중 안전장치)
        return [types.TextContent(type="text", text=_REDISPLAY_INSTRUCTION + display_text)]


    # ================================================================
    # chat_tasklog [보완 ③ — 채팅 커넥터: 근거를 인자로 받아 규격화]
    # ================================================================
    if name == "chat_tasklog":
        today = date.today().isoformat()
        # [PR#10 팔로업 — 배열 타입 방어] 문자열/비문자 항목이 와도
        # 글자 단위로 쪼개지지 않도록 배열 3종에만 정규화 적용
        # (memo/tomorrow_plan 역방향 방어는 리뷰 범위 밖이라 미적용)
        activities = _ensure_str_list(arguments.get("activities"))
        created_files = _ensure_str_list(arguments.get("created_files"))
        memo = (arguments.get("memo") or "").strip()
        tomorrow_plan = (arguments.get("tomorrow_plan") or "").strip()
        needs_confirmation = _ensure_str_list(arguments.get("needs_confirmation"))

        display_text, submission_memo = _build_chat_draft(
            today, activities, created_files, memo, tomorrow_plan, needs_confirmation
        )

        # tasklog와 캐시를 공유한다 (submit_worklog 승인 흐름 재사용).
        # 같은 날 tasklog ↔ chat_tasklog를 번갈아 호출하면 마지막 호출이
        # 캐시를 덮어쓴다 = "마지막으로 화면에 보여준 초안이 등록된다" 규칙 유지.
        # [per-user] 캐시는 사용자 스코프별로 분리 저장한다.
        draft = _current_draft()
        draft["date"] = today
        draft["submission_memo"] = submission_memo

        # [멘토 피드백 ② 동일 적용] 재표시 지시문을 tasklog와 공유 (이중 안전장치)
        return [types.TextContent(type="text", text=_REDISPLAY_INSTRUCTION + display_text)]

    # ================================================================
    # submit_worklog [리뷰 ② 반영 — _LAST_DRAFT 1회용 캐시(성공 시에만 초기화)]
    # ================================================================
    if name == "submit_worklog":
        # [per-user] 키 조회 분기 1곳: HTTP=헤더 키 / stdio=.env 키
        api_key_now = _current_api_key()
        if not api_key_now:
            if _REQUEST_IS_HTTP.get():
                return [types.TextContent(
                    type="text",
                    text=(
                        "⚠️ 커넥터에 API 키가 설정되지 않았습니다.\n"
                        "Claude 커넥터 설정 → Request Headers → x-api-key 항목에 "
                        "Waple API 키를 입력해 주세요.\n\n"
                        "API 키 발급: Waple → 환경설정 → API설정"
                    )
                )]
            return [types.TextContent(
                type="text",
                text=(
                    "⚠️ API 키가 등록되지 않았습니다.\n"
                    "먼저 waple_login 도구로 Waple API 키를 등록해 주세요.\n\n"
                    "API 키 발급: Waple → 환경설정 → API설정"
                )
            )]

        memo = arguments.get("memo")
        target_date = arguments.get("target_date") or date.today().isoformat()
        title = arguments.get("title")

        # ========================= [수정 ①] =========================
        # [지침 10번 5항 + 리뷰 ② 반영 — 1회용 캐시]
        # display_text와 submission_memo는 같은 tasklog 호출에서 함께 생성되므로,
        # 사용자가 화면에서 본 내용과 저장값은 원칙적으로 항상 일치한다.
        # target_date가 저장된 초안과 같으면, Claude가 넘긴 memo(재구성되어
        # 형식이 손실됐을 수 있음) 대신 저장된 원본을 그대로 쓴다.
        #
        # 이 캐시는 "1회용"이다:
        #   - 등록 성공 시 → 아래 성공 분기에서 즉시 비운다(소비 완료).
        #     → 같은 날짜에 tasklog 재호출 없이 예전 초안이 재사용되는 stale 방지.
        #   - 등록 실패 시 → 캐시를 유지한다.
        #     → 재시도 시 원본이 그대로 다시 전송되어야 버그 #1(재구성으로 인한
        #       구분선·tomorrow_plan 유실)이 재시도 경로로 재발하지 않기 때문.
        # [per-user] 이번 요청 사용자의 스코프 캐시만 본다 — 다른 사용자의
        # 초안이 이 사용자의 등록에 쓰이는 일을 구조적으로 차단.
        draft = _current_draft()
        used_stored_draft = False
        if draft["date"] == target_date and draft["submission_memo"]:
            memo = draft["submission_memo"]
            used_stored_draft = True
        # ===========================================================

        if not memo:
            return [types.TextContent(type="text", text="⚠️ memo(업무일지 본문)는 필수입니다.")]

        # [리뷰 ⑤ 수정] 등록 직전 키 재검증. [지침 12번: 확인 안 되면 진행하지 않음(fail-safe)]
        # 재검증 요청 자체가 실패하면 등록을 중단한다(네트워크 오류 시 무검증 등록 방지).
        try:
            key_check = _validate_api_key(api_key_now)
        except requests.exceptions.RequestException as e:
            return [types.TextContent(
                type="text",
                text=(
                    "❌ 등록 직전 API 키 재검증에 실패했습니다 (네트워크 오류).\n"
                    "잘못된 등록을 막기 위해 등록을 중단합니다. "
                    "네트워크 상태를 확인한 뒤 다시 시도해 주세요.\n\n"
                    f"(상세: {e})"
                )
            )]

        # 여기 도달하면 key_check는 항상 유효한 dict이므로 None 체크가 필요 없다.
        if not key_check["valid"]:
            return [types.TextContent(
                type="text",
                text=(
                    "❌ 저장된 API 키가 만료되었거나 무효합니다.\n"
                    "waple_login 도구로 새 API 키를 등록해 주세요."
                )
            )]
        if not key_check["has_diary_write"]:
            return [types.TextContent(
                type="text",
                text=(
                    "❌ 현재 API 키에 업무일지 등록 권한(diary:write)이 없습니다.\n"
                    "Waple에서 키를 재발급(diary:write scope 포함)한 후 "
                    "waple_login으로 다시 등록해 주세요."
                )
            )]

        try:
            existing = _check_existing_diary(target_date)
        except requests.exceptions.RequestException as e:
            return [types.TextContent(type="text", text=f"❌ 기존 일지 조회 중 네트워크 오류가 발생했습니다: {e}")]

        existing_note = ""
        if existing:
            existing_note = (
                f"\n\n📌 {target_date} 날짜에 이미 등록된 업무일지가 있어 덮어씁니다.\n"
                f"(기존 제목: {existing.get('ttl', '제목 없음')})"
            )

        try:
            result = _submit_diary(memo=memo, target_date=target_date, title=title)
        except requests.exceptions.RequestException as e:
            return [types.TextContent(type="text", text=f"❌ 업무일지 등록 중 네트워크 오류가 발생했습니다: {e}")]

        if result["ok"]:
            # ========================= [수정 ②] =========================
            # [리뷰 ② 반영] 등록 성공 → 소비된 초안이므로 캐시를 비운다.
            # (실패 분기에는 넣지 않는다 = 실패 시 캐시 유지 → 재시도 안전)
            # used_stored_draft는 위에서 이미 계산됐으므로, 여기서 캐시를 비워도
            # 방금 등록 건의 성공 메시지는 정상 출력된다. 다음 submit부터 빈 캐시를 본다.
            draft["date"] = None
            draft["submission_memo"] = None# [PR #13 이연 항목 처리] 값만 비우면 스코프 항목이 계속 남는다.
            # 등록에 성공한 시점은 이 사용자의 초안이 완전히 소비된 시점이므로
            # 항목 자체를 제거한다. 다음 tasklog 호출 때 새로 만들어지므로
            # 기능에는 영향이 없다.
            # "_local"은 _LAST_DRAFT와 같은 객체를 공유하므로 삭제하지 않는다.
            consumed_scope = _draft_scope()
            if consumed_scope != "_local":
                _LAST_DRAFTS.pop(consumed_scope, None)
            # ===========================================================

            source_note = (
                "\n\n(⚠️ 방금 전달받은 내용이 아니라, tasklog가 생성한 저장된 초안 원본이 "
                "그대로 전송되었습니다. 화면에 보신 초안과 다른 내용을 의도하셨다면 "
                "tasklog를 다시 호출해 주세요.)"
                if used_stored_draft else
                "\n\n⚠️ (저장된 tasklog 초안이 없어 전달받은 내용으로 전송했습니다. "
                "구분선 등 형식이 원본과 다를 수 있습니다.)"
            )
            text = f"✅ {result['message']}{existing_note}{source_note}"
        else:
            text = f"❌ 등록 실패 (HTTP {result['status_code']}): {result['message']}"

        return [types.TextContent(type="text", text=text)]

    raise ValueError(f"알 수 없는 도구: {name}")


# ============================================================
# FastMCP 도구 등록 — call_tool 디스패처 위임 래퍼
# [FastMCP 전환] 스키마는 함수 시그니처(+Field description)에서 자동 생성된다.
# 로직은 전부 위 call_tool에 있고, 래퍼는 인자 전달과 텍스트 추출만 한다.
# call_tool은 모든 분기에서 TextContent 1개짜리 리스트를 반환하므로
# result[0].text로 본문만 뽑아 str로 반환한다 (FastMCP가 TextContent로 감쌈).
# ============================================================

@mcp.tool(
    name="waple_login",
    description=(
        "Waple API 키를 입력받아 검증하고 로컬에 저장합니다. "
        "업무일지 등록(submit_worklog) 전에 반드시 한 번 실행해야 합니다. "
        "API 키는 Waple → 환경설정 → API설정에서 발급할 수 있습니다."
    ),
)
async def _tool_waple_login(
    ctx: Context,
    api_key: Annotated[str, Field(description="Waple에서 발급받은 API 키")] = "",
) -> str:
    _extract_request_auth(ctx)
    result = await call_tool("waple_login", {"api_key": api_key})
    return result[0].text


@mcp.tool(
    name="tasklog",
    description=(
        "오늘 작업 내역(Git 커밋, 변경 파일 규모, 사용자 메모)을 수집해 "
        "Waple 업무일지 양식(오늘 한 일 / 상세 진행 내용·특이사항 / 내일 계획)에 맞춰 초안을 생성합니다. "
        "이미 만든 초안이라도 사용자가 memo나 tomorrow_plan 등 내용을 수정해 달라고 요청하면, "
        "화면 텍스트만 직접 고쳐서 보여주지 말고 이 도구를 새 값으로 반드시 다시 호출해야 합니다. "
        "그렇지 않으면 화면에 보이는 내용과 실제 등록되는 내용이 달라집니다. "
        "[전체 초안 재표시 규칙] 이 도구가 반환한 초안은 일부만 요약하지 말고 "
        "전문 그대로 사용자에게 보여줘야 합니다. 수정을 반영해 다시 호출한 경우에도 "
        "변경 요약 한두 줄 뒤에 실제 등록될 전체 초안 전문을 매번 다시 표시하세요."
    ),
)
async def _tool_tasklog(
    ctx: Context,
    memo: Annotated[str, Field(description=(
        "Git 기록에 남지 않는 활동(회의, 설계 논의, 자료 조사 등)을 "
        "사용자가 직접 입력하는 선택 항목입니다. '오늘 한 일'에 반영됩니다."
    ))] = "",
    tomorrow_plan: Annotated[str, Field(description=(
        "'내일(향후) 계획' 항목에 들어갈 내용입니다. 사용자가 직접 불러주는 "
        "내용만 반영하며, 비어 있으면 '(미기재)'로 표시됩니다."
    ))] = "",
) -> str:
    _extract_request_auth(ctx)
    result = await call_tool("tasklog", {"memo": memo, "tomorrow_plan": tomorrow_plan})
    return result[0].text


@mcp.tool(
    name="chat_tasklog",
    description=(
        "일반 채팅(Claude 웹/앱) 환경에서 업무일지 초안을 생성합니다. "
        "Git에 접근할 수 없는 환경 전용이며, Claude Code에서는 tasklog를 사용하세요. "
        "activities에는 이 대화에서 실제로 수행·확인된 작업만 넣어야 합니다. "
        "대화에 근거가 없는 내용, 추정한 내용은 activities에 넣지 말고 "
        "needs_confirmation으로 분리하세요 (사실처럼 작성 금지). "
        "이미 만든 초안이라도 사용자가 내용 수정을 요청하면, 화면 텍스트만 "
        "고쳐서 보여주지 말고 이 도구를 새 값으로 반드시 다시 호출해야 합니다. "
        "[전체 초안 재표시 규칙] 이 도구가 반환한 초안은 일부만 요약하지 말고 "
        "전문 그대로 사용자에게 보여줘야 합니다. 수정을 반영해 다시 호출한 경우에도 "
        "변경 요약 한두 줄 뒤에 실제 등록될 전체 초안 전문을 매번 다시 표시하세요."
    ),
)
async def _tool_chat_tasklog(
    ctx: Context,
    # [PR#11 배열 타입 방어 유지] list[str]로 강제하면 Claude가 문자열 하나를
    # 보냈을 때 pydantic 검증에서 튕겨버린다. str 단일값도 허용해 통과시킨 뒤
    # call_tool 내부의 _ensure_str_list가 리스트로 정규화한다.
    activities: Annotated[list[str] | str | None, Field(description=(
        "이 대화에서 실제 수행이 확인된 작업 목록. "
        "각 항목은 '무엇을 했고 어떤 결과가 나왔는지'가 드러나는 한 문장. "
        "'오늘 한 일'에 [대화 기반] 라벨로 반영됩니다."
    ))] = None,
    created_files: Annotated[list[str] | str | None, Field(description=(
        "이 대화에서 생성하거나 수정한 파일명 목록. "
        "'상세 진행 내용'에 [생성 파일] 라벨로 반영됩니다."
    ))] = None,
    memo: Annotated[str, Field(description=(
        "대화에 남지 않은 활동(회의 등)을 사용자가 직접 불러준 내용. "
        "[사용자 메모] 라벨로 반영됩니다."
    ))] = "",
    tomorrow_plan: Annotated[str, Field(description=(
        "'내일(향후) 계획' 항목. 사용자가 직접 불러준 내용만 반영하며, "
        "비어 있으면 '(미기재)'로 표시됩니다."
    ))] = "",
    needs_confirmation: Annotated[list[str] | str | None, Field(description=(
        "수행 여부가 불분명해 본문에서 제외한 항목. "
        "Waple에 전송되지 않고 화면의 '사용자 확인 필요'에만 표시됩니다."
    ))] = None,
) -> str:
    _extract_request_auth(ctx)
    result = await call_tool("chat_tasklog", {
        "activities": activities,
        "created_files": created_files,
        "memo": memo,
        "tomorrow_plan": tomorrow_plan,
        "needs_confirmation": needs_confirmation,
    })
    return result[0].text


@mcp.tool(
    name="submit_worklog",
    description=(
        "사용자가 승인한 업무일지 내용을 Waple에 등록합니다. "
        "사용자의 명확한 승인 표현(예: '등록해', '승인', 'Waple에 저장해') 없이는 호출하지 마세요. "
        "이 도구를 사용하려면 먼저 waple_login으로 API 키를 등록해야 합니다."
    ),
)
async def _tool_submit_worklog(
    ctx: Context,
    memo: Annotated[str, Field(description="업무일지 본문 (plain text). 사용자가 최종 승인한 내용 그대로.")],
    target_date: Annotated[str, Field(description="업무일지 날짜 (yyyy-MM-dd). 미입력 시 오늘 날짜 사용.")] = "",
    title: Annotated[str, Field(description="업무일지 제목 (선택). 미입력 시 서버가 자동 생성.")] = "",
) -> str:
    # target_date=""/title=""은 call_tool 내부 `arguments.get(...) or 기본값`이
    # falsy로 처리하므로 미입력과 동작이 같다.
    _extract_request_auth(ctx)
    result = await call_tool("submit_worklog", {
        "memo": memo,
        "target_date": target_date,
        "title": title,
    })
    return result[0].text


if __name__ == "__main__":
    # [이중 transport — 7/16 갈림길 결정안, 멘토 승인 7/17]
    #   stdio(기본):        로컬 Claude Code/Desktop 연동. 인자 없이 실행하면
    #                       기존과 완전히 동일하게 동작한다 (.env 키 방식).
    #   streamable-http:    원격 커넥터용 HTTP 서버. 미니PC 배포·커넥터 등록에 사용.
    #                       엔드포인트는 http://<host>:<port>/mcp
    # 인증: stdio=.env 키 / streamable-http=매 요청 x-api-key 헤더 (per-user).
    import argparse

    parser = argparse.ArgumentParser(description="waple-tasklog MCP 서버")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="stdio(기본): 로컬 Claude Code/Desktop / streamable-http: 원격 커넥터용",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="streamable-http 바인드 주소 (기본 127.0.0.1, 외부 공개 배포 시 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="streamable-http 포트 (기본 8000)",
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)