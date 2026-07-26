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