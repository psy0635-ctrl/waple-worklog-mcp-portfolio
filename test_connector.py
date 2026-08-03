"""
chat_tasklog(보완③ 채팅 커넥터) 테스트.
네트워크·실서버 접근 없음. _build_chat_draft와 캐시 동작만 검증한다.
"""
import asyncio
import json as _json
from datetime import datetime as _datetime, timezone as _timezone
from pathlib import Path as _Path

import pytest

import server


def test_labels_and_sections():
    """[대화 기반]/[생성 파일] 라벨과 3섹션 구조가 정확한지"""
    display, memo = server._build_chat_draft(
        "2026-07-10",
        activities=["커넥터 설계 문서를 작성하였습니다"],
        created_files=["docs/connector-design.md"],
        memo="멘토 회의 참석",
        tomorrow_plan="프로토타입 테스트",
    )
    # 전송용 본문 검증
    assert "커넥터 설계 문서를 작성하였습니다 [대화 기반]" in memo
    assert "생성/수정 파일: docs/connector-design.md [생성 파일]" in memo
    assert "멘토 회의 참석 [사용자 메모]" in memo
    assert "- 프로토타입 테스트" in memo
    assert memo.count("─" * 25) == 6  # 구분선 6개 (섹션 3개 × 위아래)
    # 화면 표시용 검증
    assert "자기 보고" in display


def test_empty_inputs():
    """전부 비어 있을 때 안내 문구로 대체되는지"""
    display, memo = server._build_chat_draft(
        "2026-07-10", activities=[], created_files=[], memo="", tomorrow_plan=""
    )
    assert "오늘 확인된 활동·메모가 없습니다." in memo
    assert "특이사항 없음." in memo
    assert "(미기재)" in memo


def test_needs_confirmation_excluded_from_body():
    """확인 필요 항목이 전송 본문엔 없고 화면에만 표시되는지 [지침 6번]"""
    display, memo = server._build_chat_draft(
        "2026-07-10",
        activities=["테스트 코드를 작성하였습니다"],
        created_files=[],
        memo="",
        tomorrow_plan="",
        needs_confirmation=["배포 완료 여부 불분명"],
    )
    assert "배포 완료 여부 불분명" not in memo      # 전송 본문 제외
    assert "배포 완료 여부 불분명" in display        # 화면에는 표시


def test_cache_shared_with_submit():
    """chat_tasklog 호출 시 _LAST_DRAFT 캐시가 저장되는지"""
    server._LAST_DRAFT["date"] = None
    server._LAST_DRAFT["submission_memo"] = None

    asyncio.run(server.call_tool("chat_tasklog", {
        "activities": ["캐시 테스트 수행"],
    }))

    assert server._LAST_DRAFT["date"] is not None
    assert "캐시 테스트 수행 [대화 기반]" in server._LAST_DRAFT["submission_memo"]


# ============================================================
# [PR #12 리뷰 반영 (7/15)] 로그 폴더 대소문자 혼재 대응 테스트
# 도형준 요청 3케이스((a)정확 일치 (b)대소문자만 다른 단일 폴더
# (c)두 폴더 혼재) + 추가 3케이스(매치 없음 / 다중 폴더 합산 /
# 폴더 간 message id 중복 제거).
#
# 주의: (c) 혼재 케이스는 대소문자 구분 파일시스템에서만 폴더 2개를
# 실제로 만들 수 있음. Windows NTFS 기본 설정(대소문자 무시)에서는
# 두 번째 mkdir이 같은 폴더로 취급돼 생성 자체가 불가 → skip 처리.
# (혼재의 '탐색'은 (c)에서, 혼재의 '합산'은 FS 무관한
#  test_token_usage_sums_across_multiple_dirs에서 각각 검증)
# ============================================================


def _patch_paths(monkeypatch, tmp_path):
    """Path.home()→tmp_path, Path.cwd()→가짜 프로젝트 경로로 고정.

    가짜 cwd "C:/proj"는 치환 규칙(영숫자 외 → '-')상 폴더명 "C--proj",
    소문자 비교 기준으로는 "c--proj"가 된다. (Windows/리눅스 모두 동일 결과)
    반환값 = 가짜 ~/.claude/projects 경로 (아직 미생성 상태).
    """
    monkeypatch.setattr(server.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(server.Path, "cwd", lambda: _Path("C:/proj"))
    return tmp_path / ".claude" / "projects"


def _today_record(msg_id, input_tokens, output_tokens):
    """오늘(UTC 기준 now → 로컬 변환해도 오늘) 타임스탬프의 로그 레코드 1건."""
    ts = _datetime.now(_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }


def _write_jsonl(dir_, filename, records):
    dir_.mkdir(parents=True, exist_ok=True)
    with (dir_ / filename).open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(_json.dumps(rec) + "\n")


def test_log_dirs_exact_match(monkeypatch, tmp_path):
    """(a) 정확 일치 폴더 1개 → 그 폴더만 반환 (기존 동작 보존)."""
    projects = _patch_paths(monkeypatch, tmp_path)
    (projects / "C--proj").mkdir(parents=True)
    dirs = server._get_claude_projects_log_dirs()
    assert [d.name for d in dirs] == ["C--proj"]


def test_log_dirs_case_variant_single(monkeypatch, tmp_path):
    """(b) 대소문자만 다른 단일 폴더 → 폴백 없이도 매치 (소문자 비교)."""
    projects = _patch_paths(monkeypatch, tmp_path)
    (projects / "c--PROJ").mkdir(parents=True)
    dirs = server._get_claude_projects_log_dirs()
    assert [d.name for d in dirs] == ["c--PROJ"]


def test_log_dirs_mixed_case_returns_all(monkeypatch, tmp_path):
    """(c) 대소문자 다른 폴더 2개 혼재 → 하나만 고르지 않고 전부 반환 (블로커 핵심①)."""
    projects = _patch_paths(monkeypatch, tmp_path)
    (projects / "C--proj").mkdir(parents=True)
    try:
        (projects / "c--proj").mkdir()
    except FileExistsError:
        pytest.skip("대소문자 무시 FS(Windows NTFS 기본) — 혼재 폴더 생성 불가, "
                    "리눅스/CI에서 검증됨")
    dirs = server._get_claude_projects_log_dirs()
    assert sorted(d.name for d in dirs) == ["C--proj", "c--proj"]


def test_log_dirs_no_match_returns_empty(monkeypatch, tmp_path):
    """매치 폴더 없음/루트 자체 없음 → 빈 리스트, 토큰은 '수집 불가'(None)."""
    projects = _patch_paths(monkeypatch, tmp_path)
    # ~/.claude/projects 자체가 없음 → iterdir OSError 경로 → 빈 리스트
    assert server._get_claude_projects_log_dirs() == []
    projects.mkdir(parents=True)
    (projects / "other-proj").mkdir()  # 루트는 있으나 매치 폴더 없음
    assert server._get_claude_projects_log_dirs() == []
    assert server._collect_today_token_usage() is None


def test_token_usage_sums_across_multiple_dirs(monkeypatch, tmp_path):
    """혼재 폴더의 jsonl 전부 합산 (블로커 핵심②). FS 무관 검증을 위해
    폴더 탐색 함수를 두 폴더 반환으로 고정하고 합산 로직만 본다."""
    dir_a, dir_b = tmp_path / "A", tmp_path / "B"
    _write_jsonl(dir_a, "s1.jsonl", [_today_record("m1", 10, 5)])
    _write_jsonl(dir_b, "s2.jsonl", [_today_record("m2", 100, 50)])
    monkeypatch.setattr(server, "_get_claude_projects_log_dirs",
                        lambda: [dir_a, dir_b])
    usage = server._collect_today_token_usage()
    assert usage is not None
    assert usage["input"] == 110 and usage["output"] == 55
    assert usage["messages"] == 2
    assert usage["total"] == 165


def test_token_usage_dedups_same_id_across_dirs(monkeypatch, tmp_path):
    """같은 message id가 두 폴더에 중복 기록돼도 1회만 집계
    (도형준 제안의 전제 'per_id_usage 중복 제거로 안전'을 실측 검증)."""
    dir_a, dir_b = tmp_path / "A", tmp_path / "B"
    _write_jsonl(dir_a, "s1.jsonl", [_today_record("m1", 10, 5)])
    _write_jsonl(dir_b, "s2.jsonl", [_today_record("m1", 10, 5)])  # 동일 id
    monkeypatch.setattr(server, "_get_claude_projects_log_dirs",
                        lambda: [dir_a, dir_b])
    usage = server._collect_today_token_usage()
    assert usage["input"] == 10 and usage["output"] == 5
    assert usage["messages"] == 1

# ── [feature/http-transport] per-user 스코핑·키 분기 검증 ─────────────────

def test_draft_scope_local_shares_legacy_dict():
    """_local 스코프는 기존 _LAST_DRAFT와 같은 객체를 공유한다 (stdio·기존 테스트 호환)."""
    assert server._LAST_DRAFTS["_local"] is server._LAST_DRAFT


def test_draft_scope_isolated_between_users():
    """HTTP 사용자(키)별로 초안 캐시가 격리된다 — A의 초안이 B·로컬 스코프에 안 보임."""
    server._LAST_DRAFT["date"] = None
    server._LAST_DRAFT["submission_memo"] = None
    token = server._REQUEST_API_KEY.set("user-A-key")
    try:
        asyncio.run(server.call_tool("chat_tasklog", {"activities": ["A의 작업"]}))
        scope_a = server._draft_scope()
    finally:
        server._REQUEST_API_KEY.reset(token)
    token = server._REQUEST_API_KEY.set("user-B-key")
    try:
        scope_b = server._draft_scope()
        b_draft = server._LAST_DRAFTS.get(scope_b)
    finally:
        server._REQUEST_API_KEY.reset(token)
    assert scope_a != scope_b
    assert "A의 작업" in server._LAST_DRAFTS[scope_a]["submission_memo"]
    assert b_draft is None or b_draft["submission_memo"] is None
    assert server._LAST_DRAFT["submission_memo"] is None  # 로컬 스코프 오염 없음
    server._LAST_DRAFTS.pop(scope_a, None)  # 테스트 간 오염 방지


def test_current_api_key_http_uses_header(monkeypatch):
    """HTTP 모드: x-api-key 헤더 키를 사용한다 (.env 키가 있어도 헤더 우선)."""
    monkeypatch.setattr(server, "WAPLE_API_KEY", "env-key")
    token_http = server._REQUEST_IS_HTTP.set(True)
    token_key = server._REQUEST_API_KEY.set("header-key")
    try:
        assert server._current_api_key() == "header-key"
    finally:
        server._REQUEST_API_KEY.reset(token_key)
        server._REQUEST_IS_HTTP.reset(token_http)


def test_current_api_key_http_no_header_blocks_env_fallback(monkeypatch):
    """[리뷰 ①] HTTP 모드 + 헤더 없음: 서버 .env에 키가 남아 있어도
    None을 반환한다 (.env 폴백 차단 — 사용자 간 키 공유 방지)."""
    monkeypatch.setattr(server, "WAPLE_API_KEY", "env-key-should-not-leak")
    token_http = server._REQUEST_IS_HTTP.set(True)
    token_key = server._REQUEST_API_KEY.set(None)
    try:
        assert server._current_api_key() is None
    finally:
        server._REQUEST_API_KEY.reset(token_key)
        server._REQUEST_IS_HTTP.reset(token_http)


def test_current_api_key_stdio_uses_env(monkeypatch):
    """stdio 모드(기본값): 기존대로 .env의 WAPLE_API_KEY를 사용한다 (동작 불변)."""
    monkeypatch.setattr(server, "WAPLE_API_KEY", "env-key")
    assert server._current_api_key() == "env-key"


def test_draft_scope_name_hides_key():
    """스코프명은 sha256 16자리 — API 키 원문이 dict 키로 노출되지 않는다."""
    token = server._REQUEST_API_KEY.set("secret-raw-key")
    try:
        scope = server._draft_scope()
    finally:
        server._REQUEST_API_KEY.reset(token)
    assert "secret-raw-key" not in scope
    assert len(scope) == 16
    server._LAST_DRAFTS.pop(scope, None)

# ── [fix/remote-tasklog-guard] 원격(HTTP) 모드 Git 수집 차단 검증 ──────────

def test_tasklog_blocked_in_http_mode():
    """원격(HTTP) 모드에서 tasklog가 Git·토큰을 수집하지 않고 차단되는지.

    실제 문제: 원격 서버의 tasklog는 사용자 PC가 아니라 배포 서버의 저장소를
    읽어, 사용자가 하지 않은 커밋이 업무일지 본문에 사실처럼 실렸다.
    """
    server._LAST_DRAFT["date"] = None
    server._LAST_DRAFT["submission_memo"] = None

    token = server._REQUEST_IS_HTTP.set(True)   # HTTP 요청 상황 재현
    try:
        result = asyncio.run(server.call_tool("tasklog", {"memo": "원격 호출"}))
    finally:
        server._REQUEST_IS_HTTP.reset(token)    # 다른 테스트로 값이 새지 않게 복구

    text = result[0].text
    assert "chat_tasklog" in text                     # 대체 경로를 안내한다
    assert "오늘 한 일" not in text                    # 초안이 생성되지 않았다
    assert "Git 저장소를 찾을 수 없습니다" not in text  # repo_root 분기로 새지 않았다
    assert server._LAST_DRAFT["date"] is None         # 초안 캐시 미오염


def test_tasklog_block_does_not_pollute_user_scope_cache():
    """HTTP 사용자 스코프 캐시에도 초안이 남지 않는지 (submit_worklog 오등록 방지)."""
    token_http = server._REQUEST_IS_HTTP.set(True)
    token_key = server._REQUEST_API_KEY.set("remote-user-key")
    try:
        scope = server._draft_scope()
        asyncio.run(server.call_tool("tasklog", {"memo": "원격 호출"}))
        cached = server._LAST_DRAFTS.get(scope)
    finally:
        server._REQUEST_API_KEY.reset(token_key)
        server._REQUEST_IS_HTTP.reset(token_http)

    assert cached is None or cached["submission_memo"] is None
    server._LAST_DRAFTS.pop(scope, None)  # 테스트 간 오염 방지


def test_tasklog_still_runs_in_stdio_mode(monkeypatch):
    """stdio(로컬) 모드는 기존대로 Git 수집 경로를 탄다 — 회귀 방지.

    실제 git 실행에 의존하지 않도록 수집 함수를 전부 가짜로 바꾼다.
    """
    monkeypatch.setattr(server, "_get_repo_root", lambda: "/fake/repo")
    monkeypatch.setattr(server, "_collect_today_commits", lambda root: [])
    monkeypatch.setattr(server, "_collect_git_status",
                        lambda root: {"staged": [], "unstaged": []})
    monkeypatch.setattr(server, "_collect_git_diff_stat", lambda root: [])
    monkeypatch.setattr(server, "_collect_today_token_usage", lambda: None)

    result = asyncio.run(server.call_tool("tasklog", {"memo": "로컬 호출"}))
    text = result[0].text
    assert "원격 연결에서는" not in text   # 차단 메시지가 아니다
    assert "로컬 호출" in text            # 초안이 정상 생성됐다

    for scope in list(server._LAST_DRAFTS):
        if scope != "_local":
            server._LAST_DRAFTS.pop(scope, None)


# ── [fix/auth-guide] 인증 실패 안내가 실재하는 경로만 지시하는지 ──────────

def _http_reply(tool, args):
    """HTTP(원격) 모드로 도구를 호출해 응답 텍스트를 돌려준다.

    키가 없으면 두 도구 모두 조기 반환하므로 네트워크 요청은 발생하지 않는다.
    """
    token = server._REQUEST_IS_HTTP.set(True)
    try:
        result = asyncio.run(server.call_tool(tool, args))
    finally:
        server._REQUEST_IS_HTTP.reset(token)
    return result[0].text


def test_auth_guide_does_not_point_to_nonexistent_ui():
    """존재하지 않는 UI("커넥터 설정 → Request Headers")를 지시하지 않는지.

    claude.ai 웹 커넥터에는 커스텀 헤더 입력란이 없다(7/28 설정 화면 확인).
    종전 안내는 사용자를 막다른 길로 보냈다.
    """
    for tool, args in [("submit_worklog", {"memo": "x"}), ("waple_login", {})]:
        text = _http_reply(tool, args)
        assert "Request Headers" not in text, tool


def test_auth_guide_lists_working_paths():
    """실제로 헤더를 지정할 수 있는 3경로를 모두 안내하는지."""
    for tool, args in [("submit_worklog", {"memo": "x"}), ("waple_login", {})]:
        text = _http_reply(tool, args)
        assert "claude mcp add" in text, tool          # Claude Code
        assert "mcp-remote" in text, tool               # 데스크톱 브리지
        assert "stdio" in text, tool                    # 로컬 실행
        assert "웹 커넥터" in text, tool                 # 불가 경로 명시


def test_auth_guide_is_shared_by_both_tools():
    """두 도구가 같은 상수를 참조하는지 — 한쪽만 고쳐 어긋나는 것을 방지."""
    a = _http_reply("submit_worklog", {"memo": "x"})
    b = _http_reply("waple_login", {})
    assert server._HEADER_AUTH_GUIDE in a
    assert server._HEADER_AUTH_GUIDE in b


# ── [투명 포맷 전환] 재표시 지시문 출처 노출 확인 ──────────────────────

def test_redisplay_instruction_no_longer_hides_from_user():
    """"사용자에게 보여주지 마세요" 류 은닉 지시가 다시 들어오지 않는지.

    형태를 좁게 잡으면 다른 표현으로 재발할 수 있어 여러 변형을 검사한다
    (8/1 check_secrets 패턴 확장과 같은 취지).
    """
    banned = [
        "보여주지 마세요",
        "사용자에게 보여주지",
        "숨기",
        "표시하지 마",
        "노출하지 마",
        "출력하지 마",
        "문단은 생략",
        "안내는 생략",
    ]
    for phrase in banned:
        assert phrase not in server._REDISPLAY_INSTRUCTION, phrase


def test_redisplay_instruction_discloses_source():
    """이 문단이 waple-worklog-mcp 서버가 삽입한 것임을 명시하는지."""
    assert "waple-worklog-mcp" in server._REDISPLAY_INSTRUCTION


def test_redisplay_instruction_keeps_required_functions():
    """요약 금지·재호출 시 재표시·등록 대상 전체라는 사실 3가지가 남아있는지."""
    text = server._REDISPLAY_INSTRUCTION
    assert "요약" in text        # ① 요약하지 말 것
    assert "다시" in text        # ② 재호출 시 재표시
    assert "Waple" in text       # ③ 등록 대상 전체임을 알림


def test_redisplay_instruction_is_shared_by_both_tools(monkeypatch):
    """tasklog·chat_tasklog 두 도구가 같은 상수를 참조하는지."""
    monkeypatch.setattr(server, "_get_repo_root", lambda: "/fake/repo")
    monkeypatch.setattr(server, "_collect_today_commits", lambda root: [])
    monkeypatch.setattr(server, "_collect_git_status",
                        lambda root: {"staged": [], "unstaged": []})
    monkeypatch.setattr(server, "_collect_git_diff_stat", lambda root: [])
    monkeypatch.setattr(server, "_collect_today_token_usage", lambda: None)

    a = asyncio.run(server.call_tool("tasklog", {"memo": "x"}))[0].text
    b = asyncio.run(server.call_tool("chat_tasklog", {"memo": "x"}))[0].text
    assert server._REDISPLAY_INSTRUCTION in a
    assert server._REDISPLAY_INSTRUCTION in b

    # _local은 _LAST_DRAFT와 같은 객체를 공유 — 지우면 stdio 경로가 조용히 깨짐
    for scope in list(server._LAST_DRAFTS):
        if scope != "_local":
            server._LAST_DRAFTS.pop(scope, None)