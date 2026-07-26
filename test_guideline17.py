# -*- coding: utf-8 -*-
"""
지침 17번 추가 검증 — 9(필수 필드 누락) · 11(중복 등록) · 12(성공 여부 불분명)

목적:
  test_cache.py가 다룬 5·10·11(캐시 경로) 외에, submit 경로에서 모킹으로
  검증 가능한 나머지 항목을 확인한다. 실제 Waple 서버에는 요청하지 않는다.

실행 위치:
  server.py와 같은 폴더(llm팀/)에서 실행한다.
      cd C:\\study\\uxis\\2026-uxis-mirae\\llm팀
      python test_guideline17.py

기대 출력:
  9 · 11 · 12 세 항목이 모두 [PASS ✅]로 나오면 정상이다.
"""

import asyncio
import inspect

import server  # 같은 폴더의 server.py


TODAY = server.date.today().isoformat()
_ORIG_SUBMIT_DIARY = server._submit_diary

# 0. call_tool 직접 호출 가능 여부 사전 점검
if not inspect.iscoroutinefunction(server.call_tool):
    print("⚠️ server.call_tool을 직접 호출할 수 없는 SDK 버전입니다.")
    print("   → MCP 클라이언트 통합 테스트가 필요합니다.")
    raise SystemExit(1)


# 공통 세팅 (가짜 키 / BASE_URL)
server.WAPLE_API_KEY = "test-key-abcd"
if not getattr(server, "WAPLE_BASE_URL", None):
    server.WAPLE_BASE_URL = "https://waple.example.test"


def reset_cache():
    """각 케이스 사이 캐시 오염 방지."""
    server._LAST_DRAFT["date"] = None
    server._LAST_DRAFT["submission_memo"] = None


def fake_validate_ok(api_key):
    return {
        "valid": True,
        "has_diary_write": True,
        "scopes": ["diary:write"],
        "message": "ok",
        "status_code": 200,
    }


async def call_submit(arguments: dict) -> str:
    res = await server.call_tool("submit_worklog", arguments)
    return res[0].text


# ============================================================
# 케이스 9 — 필수 필드(memo) 누락
#   memo 없이 등록을 시도하면, 등록을 하지 않고 안내만 나와야 한다.
# ============================================================
async def case_9():
    reset_cache()

    submitted = []  # _submit_diary가 불렸는지 감시(스파이)

    def spy_submit(memo, target_date, title=None):
        submitted.append(memo)
        return {"ok": True, "message": "등록 완료", "status_code": 200}

    server._submit_diary = spy_submit
    server._validate_api_key = fake_validate_ok
    server._check_existing_diary = lambda d: None

    txt = await call_submit({"target_date": TODAY})  # memo 생략

    checks = {
        "필수 안내(⚠️ memo 필수) 출력": ("memo" in txt and "필수" in txt),
        "등록 시도 안 함(_submit_diary 미호출)": (submitted == []),
    }
    return all(checks.values()), {**checks, "응답": txt.strip()[:40]}


# ============================================================
# 케이스 11 — 중복(같은 날짜 기존 일지 존재) → 덮어쓰기 안내
#   Waple은 Upsert라 같은 날짜면 덮어쓴다. 그 사실을 사용자에게 알려야 한다.
# ============================================================
async def case_11():
    reset_cache()

    def fake_existing(target_date):
        return {"ttl": "기존 제목", "taskBgngYmd": target_date}

    def ok_submit(memo, target_date, title=None):
        return {"ok": True, "message": "등록 완료", "status_code": 200}

    server._validate_api_key = fake_validate_ok
    server._check_existing_diary = fake_existing
    server._submit_diary = ok_submit

    txt = await call_submit({"memo": "오늘 한 일 본문", "target_date": TODAY})

    checks = {
        "성공 보고(✅)": "✅" in txt,
        "덮어쓰기 안내 포함": "덮어씁니다" in txt,
        "기존 제목 노출": "기존 제목" in txt,
    }
    return all(checks.values()), {**checks, "응답": txt.strip().replace("\n", " ")[:70]}


# ============================================================
# 케이스 12 — 성공 여부 불분명 → 안전하게 '실패'로 처리
#   _submit_diary가 애매한 HTTP 응답을 성공으로 오판하지 않는지 확인한다.
#   (requests.post를 가짜 응답 객체로 교체)
# ============================================================
def make_fake_post(status_code, json_data=None, raise_json=False):
    class FakeResp:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if raise_json:
                raise ValueError("not json")
            return json_data

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResp()

    return fake_post


def case_12():
    server._submit_diary = _ORIG_SUBMIT_DIARY
    results = {}

    # 12-1: HTTP 200 + status:false → 실패로 처리해야 함
    server.requests.post = make_fake_post(200, {"status": False, "message": "권한 없음"})
    r1 = server._submit_diary("본문", TODAY)
    results["12-1 status:false → ok=False"] = (r1["ok"] is False)

    # 12-2: JSON 파싱 실패(HTML 등) → 실패 + '해석' 안내
    server.requests.post = make_fake_post(200, raise_json=True)
    r2 = server._submit_diary("본문", TODAY)
    results["12-2 JSON 파싱 실패 → ok=False"] = (r2["ok"] is False and "해석" in r2["message"])

    # 12-3: status 필드 자체가 없음 → 안전하게 실패
    server.requests.post = make_fake_post(200, {"message": "성공인지 불명"})
    r3 = server._submit_diary("본문", TODAY)
    results["12-3 status 필드 없음 → ok=False"] = (r3["ok"] is False)

    # 12-4 (대조군): 정상 성공 → ok=True 여야 판단 로직이 살아있음을 증명
    server.requests.post = make_fake_post(200, {"status": True, "message": "등록완료"})
    r4 = server._submit_diary("본문", TODAY)
    results["12-4 (대조) status:true → ok=True"] = (r4["ok"] is True)

    return all(results.values()), results


# ============================================================
# 실행
# ============================================================
async def main():
    print("=" * 54)
    print(" 지침 17번 추가 검증 — 9 · 11 · 12 (실서버 요청 없음)")
    print("=" * 54)

    all_pass = True

    p9, d9 = await case_9()
    all_pass = all_pass and p9
    print(f"\n[{'PASS ✅' if p9 else 'FAIL ❌'}] 9. 필수 필드(memo) 누락")
    for k, v in d9.items():
        print(f"      - {k}: {v}")

    p11, d11 = await case_11()
    all_pass = all_pass and p11
    print(f"\n[{'PASS ✅' if p11 else 'FAIL ❌'}] 11. 중복(같은 날짜) → 덮어쓰기 안내")
    for k, v in d11.items():
        print(f"      - {k}: {v}")

    p12, d12 = case_12()
    all_pass = all_pass and p12
    print(f"\n[{'PASS ✅' if p12 else 'FAIL ❌'}] 12. 성공 여부 불분명 → 안전하게 실패 처리")
    for k, v in d12.items():
        print(f"      - {k}: {v}")

    print("\n" + "=" * 54)
    print(" 전체 결과:", "모두 통과 ✅" if all_pass else "실패 항목 있음 ❌")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())