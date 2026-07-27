"""
초안 캐시(_LAST_DRAFTS) 누적 방지 테스트.

PR #13 리뷰에서 "사용자별 스코프 항목이 계속 쌓인다"는 지적을 받고
다음 배포에서 처리하기로 이연했던 항목을 검증한다.
네트워크 접근은 전부 모킹하므로 실서버에 요청이 나가지 않는다.
"""
import asyncio
import server


def _reset_drafts():
    """테스트 간 간섭을 막기 위해 _local 만 남기고 초기화한다."""
    for scope in [s for s in server._LAST_DRAFTS if s != "_local"]:
        del server._LAST_DRAFTS[scope]
    server._LAST_DRAFT["date"] = None
    server._LAST_DRAFT["submission_memo"] = None


def test_local_scope_shares_object_with_last_draft():
    """[회귀 방지] _local 스코프가 _LAST_DRAFT와 같은 객체를 계속 공유하는지.

    이 관계가 끊어지면 stdio 경로와 기존 캐시 테스트가 조용히 깨진다.
    정리 로직이 _local 을 건드리지 않는다는 것이 이 프로젝트의 전제다.
    """
    _reset_drafts()
    assert server._LAST_DRAFTS["_local"] is server._LAST_DRAFT

    draft = server._current_draft()  # 헤더 키가 없으므로 _local 스코프
    assert draft is server._LAST_DRAFT


def test_prune_removes_stale_scope():
    """TTL이 지난 사용자 스코프가 정리되는지"""
    _reset_drafts()
    server._LAST_DRAFTS["olduser"] = {
        "date": "2026-07-01",
        "submission_memo": "예전 초안",
        "updated_at": 1000.0,
    }
    # 기준 시각을 TTL 이후로 잡는다
    removed = server._prune_draft_scopes(now=1000.0 + server._DRAFT_TTL_SECONDS + 1)

    assert removed == 1
    assert "olduser" not in server._LAST_DRAFTS


def test_prune_keeps_fresh_scope():
    """TTL 이내의 활성 사용자 초안은 유지되는지"""
    _reset_drafts()
    server._LAST_DRAFTS["activeuser"] = {
        "date": "2026-07-27",
        "submission_memo": "작성 중인 초안",
        "updated_at": 1000.0,
    }
    removed = server._prune_draft_scopes(now=1000.0 + 60)

    assert removed == 0
    assert "activeuser" in server._LAST_DRAFTS


def test_prune_never_removes_local():
    """_local 은 updated_at 이 없어도 정리 대상에서 제외되는지"""
    _reset_drafts()
    assert "updated_at" not in server._LAST_DRAFT

    removed = server._prune_draft_scopes(now=10**12)  # 아주 먼 미래

    assert removed == 0
    assert "_local" in server._LAST_DRAFTS
    assert server._LAST_DRAFTS["_local"] is server._LAST_DRAFT


def test_current_draft_records_updated_at_for_http_scope():
    """HTTP 사용자 스코프 생성 시 최근 사용 시각이 기록되는지"""
    _reset_drafts()
    token = server._REQUEST_API_KEY.set("TESTKEY-A")
    try:
        draft = server._current_draft()
        scope = server._draft_scope()
    finally:
        server._REQUEST_API_KEY.reset(token)

    assert scope != "_local"
    assert scope in server._LAST_DRAFTS
    assert draft["updated_at"] > 0


def test_scope_removed_after_successful_submit(monkeypatch):
    """등록에 성공하면 사용자 스코프 항목 자체가 사라지는지 (핵심 검증)"""
    _reset_drafts()

    # 네트워크 호출 전부 차단 — 실서버에 요청이 나가지 않는다
    monkeypatch.setattr(
        server, "_validate_api_key",
        lambda api_key: {"valid": True, "has_diary_write": True},
    )
    monkeypatch.setattr(server, "_check_existing_diary", lambda target_date: None)
    monkeypatch.setattr(
        server, "_submit_diary",
        lambda memo, target_date, title=None: {
            "ok": True, "status_code": 200, "message": "등록 완료",
        },
    )

    token_http = server._REQUEST_IS_HTTP.set(True)
    token_key = server._REQUEST_API_KEY.set("TESTKEY-B")
    try:
        scope = server._draft_scope()
        draft = server._current_draft()
        draft["date"] = "2026-07-27"
        draft["submission_memo"] = "초안 본문"
        assert scope in server._LAST_DRAFTS  # 등록 전에는 존재

        result = asyncio.run(server.call_tool(
            "submit_worklog",
            {"memo": "초안 본문", "target_date": "2026-07-27"},
        ))
    finally:
        server._REQUEST_API_KEY.reset(token_key)
        server._REQUEST_IS_HTTP.reset(token_http)

    assert "✅" in result[0].text
    assert scope not in server._LAST_DRAFTS  # 등록 후에는 항목 자체가 사라짐


def test_local_scope_survives_successful_submit(monkeypatch):
    """[회귀 방지] stdio 경로에서는 등록 후에도 _local 항목이 유지되는지"""
    _reset_drafts()

    monkeypatch.setattr(
        server, "_validate_api_key",
        lambda api_key: {"valid": True, "has_diary_write": True},
    )
    monkeypatch.setattr(server, "_check_existing_diary", lambda target_date: None)
    monkeypatch.setattr(
        server, "_submit_diary",
        lambda memo, target_date, title=None: {
            "ok": True, "status_code": 200, "message": "등록 완료",
        },
    )
    monkeypatch.setattr(server, "_current_api_key", lambda: "LOCALKEY")

    draft = server._current_draft()  # _local
    draft["date"] = "2026-07-27"
    draft["submission_memo"] = "로컬 초안"

    result = asyncio.run(server.call_tool(
        "submit_worklog",
        {"memo": "로컬 초안", "target_date": "2026-07-27"},
    ))

    assert "✅" in result[0].text
    assert "_local" in server._LAST_DRAFTS
    assert server._LAST_DRAFTS["_local"] is server._LAST_DRAFT
    assert server._LAST_DRAFT["submission_memo"] is None  # 값은 비워짐
