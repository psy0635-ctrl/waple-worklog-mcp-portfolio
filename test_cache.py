# -*- coding: utf-8 -*-
"""
_LAST_DRAFT 1회용 캐시 검증 스크립트 (리뷰 ② 반영 확인)

목적:
  submit_worklog의 캐시가 "성공 시에만 초기화, 실패 시 유지"로
  동작하는지 확인한다. 실제 Waple 서버에는 전혀 요청하지 않는다
  (네트워크 함수를 모두 가짜로 교체 = monkeypatch).

실행 위치:
  이 파일을 server.py와 같은 폴더(llm팀/)에 두고 그 폴더에서 실행한다.
      cd C:\\study\\uxis\\2026-uxis-mirae\\llm팀
      python test_cache.py

기대 출력:
  세 케이스(A/B/C)가 모두 [PASS ✅]로 나오면 재설계가 정상 반영된 것이다.
"""

import asyncio
import inspect

import requests  # 네트워크 예외(ConnectionError)를 흉내내기 위해 필요

import server  # 같은 폴더의 server.py를 불러온다


TODAY = server.date.today().isoformat()


# ============================================================
# 0. 사전 점검: call_tool을 직접 호출할 수 있는가?
#    MCP SDK의 @app.call_tool() 데코레이터가 원본 함수를 그대로
#    돌려주면 직접 호출이 된다. SDK 버전에 따라 다를 수 있어 먼저 확인한다.
# ============================================================
if not inspect.iscoroutinefunction(server.call_tool):
    print("⚠️ server.call_tool을 직접 호출할 수 없는 SDK 버전입니다.")
    print("   (데코레이터가 원본 async 함수를 반환하지 않음)")
    print("   → 이 스크립트 대신 MCP 클라이언트로 통합 테스트가 필요합니다.")
    raise SystemExit(1)


# ============================================================
# 1. 공통 가짜 함수 (네트워크 차단용)
# ============================================================
def fake_validate_ok(api_key):
    """등록 직전 키 재검증 → 항상 '유효 + diary:write 있음'으로 응답."""
    return {
        "valid": True,
        "has_diary_write": True,
        "scopes": ["diary:write"],
        "message": "ok",
        "status_code": 200,
    }


def fake_check_none(target_date):
    """기존 일지 조회 → 항상 '없음'으로 응답(덮어쓰기 안내 생략)."""
    return None


# 실제 네트워크로 나가는 함수/키를 전부 가짜로 교체한다.
server.WAPLE_API_KEY = "test-key-abcd"          # 키 존재 조건 통과용(가짜)
server._validate_api_key = fake_validate_ok      # 등록 직전 재검증 우회
server._check_existing_diary = fake_check_none   # 기존 일지 조회 우회


# ============================================================
# 2. 헬퍼: 캐시 세팅 / 조회 / submit 호출
# ============================================================
def set_cache(memo: str) -> None:
    """tasklog가 초안을 저장한 상태를 흉내낸다."""
    server._LAST_DRAFT["date"] = TODAY
    server._LAST_DRAFT["submission_memo"] = memo


def cache_state():
    """현재 캐시 상태를 (date, memo) 튜플로 반환."""
    return (server._LAST_DRAFT["date"], server._LAST_DRAFT["submission_memo"])


async def call_submit(passed_memo: str):
    """submit_worklog 도구를 호출하고 결과 텍스트를 반환한다.

    passed_memo: Claude가 넘긴 것으로 가정하는 memo.
      캐시가 살아 있으면 이 값은 무시되고 캐시 원본이 쓰여야 정상이다.
    """
    res = await server.call_tool(
        "submit_worklog",
        {"memo": passed_memo, "target_date": TODAY},
    )
    return res[0].text


# ============================================================
# 3. 케이스 A — 등록 성공 → 캐시 비워짐 + 원본 전송
# ============================================================
async def case_A():
    submitted = []  # _submit_diary에 실제로 전달된 memo를 기록(스파이)

    def fake_submit_ok(memo, target_date, title=None):
        submitted.append(memo)
        return {"ok": True, "message": "등록 완료", "status_code": 200}

    server._submit_diary = fake_submit_ok

    set_cache("원본초안-A")                       # tasklog가 저장한 원본
    txt = await call_submit("재구성된-엉뚱한-memo")  # Claude가 넘긴 값(무시돼야 함)

    checks = {
        "성공응답(✅ 포함)": "✅" in txt,
        "원본이 전송됨": submitted == ["원본초안-A"],
        "등록 후 캐시 비움": cache_state() == (None, None),
    }
    return all(checks.values()), {**checks, "전송된memo": submitted, "캐시상태": cache_state()}


# ============================================================
# 4. 케이스 B — 네트워크 실패 → 캐시 유지 → 재시도 시 원본 재전송
#    (버그 #1 재발 방지의 핵심 증명)
# ============================================================
async def case_B():
    # (1) 첫 시도: 네트워크 오류로 실패
    def fake_submit_fail(memo, target_date, title=None):
        raise requests.exceptions.ConnectionError("네트워크 끊김(테스트)")

    server._submit_diary = fake_submit_fail

    set_cache("원본초안-B")
    txt1 = await call_submit("재구성memo-1")

    first_fail_reported = "❌" in txt1
    cache_kept_after_fail = cache_state() == (TODAY, "원본초안-B")

    # (2) 재시도: 이번엔 성공. 캐시가 유지됐다면 '원본초안-B'가 전송돼야 한다.
    submitted = []

    def fake_submit_ok(memo, target_date, title=None):
        submitted.append(memo)
        return {"ok": True, "message": "등록 완료", "status_code": 200}

    server._submit_diary = fake_submit_ok

    txt2 = await call_submit("재구성memo-2")  # 또 엉뚱한 값이 넘어와도

    retry_sent_original = submitted == ["원본초안-B"]   # 원본이 나가야 정상
    cache_cleared_after_success = cache_state() == (None, None)

    checks = {
        "1차: 실패 보고(❌)": first_fail_reported,
        "1차 실패 후 캐시 유지": cache_kept_after_fail,
        "재시도: 원본 재전송": retry_sent_original,
        "재시도 성공 후 캐시 비움": cache_cleared_after_success,
    }
    return all(checks.values()), {**checks, "재시도 전송memo": submitted}


# ============================================================
# 5. 케이스 C — 서버 5xx(ok=False) → 캐시 유지
# ============================================================
async def case_C():
    def fake_submit_5xx(memo, target_date, title=None):
        return {"ok": False, "message": "server error", "status_code": 500}

    server._submit_diary = fake_submit_5xx

    set_cache("원본초안-C")
    txt = await call_submit("재구성memo")

    checks = {
        "실패 보고(❌ 등록 실패)": "❌ 등록 실패" in txt,
        "5xx 후 캐시 유지": cache_state() == (TODAY, "원본초안-C"),
    }
    return all(checks.values()), {**checks, "캐시상태": cache_state()}


# ============================================================
# 6. 실행 및 결과 표
# ============================================================
async def main():
    cases = [
        ("A 성공 → 캐시비움 + 원본전송", case_A),
        ("B 네트워크실패 → 캐시유지 + 재시도원본", case_B),
        ("C 5xx → 캐시유지", case_C),
    ]

    all_pass = True
    print("=" * 52)
    print(" _LAST_DRAFT 1회용 캐시 검증 (실서버 요청 없음)")
    print("=" * 52)

    for title, fn in cases:
        passed, detail = await fn()
        all_pass = all_pass and passed
        mark = "PASS ✅" if passed else "FAIL ❌"
        print(f"\n[{mark}] {title}")
        for k, v in detail.items():
            print(f"      - {k}: {v}")

    print("\n" + "=" * 52)
    print(" 전체 결과:", "모두 통과 ✅" if all_pass else "실패 항목 있음 ❌")
    print("=" * 52)


if __name__ == "__main__":
    asyncio.run(main())