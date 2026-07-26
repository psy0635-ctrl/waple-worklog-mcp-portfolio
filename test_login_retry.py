# -*- coding: utf-8 -*-
"""
waple_login 재시도 안내(UX ②) 검증 — 타임아웃 / 연결오류

목적:
  waple_login 호출 중 네트워크 예외(Timeout / ConnectionError)가 났을 때,
  응답 텍스트가 "재시도 방법(같은 키로 다시 실행)"과 "재시도 안전성(키 미저장)"을
  안내하는지 확인한다. 실제 Waple 서버에는 요청하지 않는다(_validate_api_key 모킹).

  또한 이 실패 경로에서 .env에 키가 저장되지 않는지(_save_api_key_to_env 미호출)도
  스파이로 확인한다 — 안내 문구("아직 저장되지 않았으므로")가 코드와 일치함을 증명.

실행 위치:
  server.py와 같은 폴더(llm팀/)에서 실행한다.
      cd C:\\study\\uxis\\2026-uxis-mirae\\llm팀
      python test_login_retry.py

기대 출력:
  두 케이스(타임아웃 / 연결오류)가 모두 [PASS ✅]면 정상이다.
"""

import asyncio
import inspect

import requests

import server


# 0. call_tool 직접 호출 가능 여부 사전 점검
if not inspect.iscoroutinefunction(server.call_tool):
    print("⚠️ server.call_tool을 직접 호출할 수 없는 SDK 버전입니다.")
    print("   → MCP 클라이언트 통합 테스트가 필요합니다.")
    raise SystemExit(1)

# 원본 보존(테스트 격리: 끝나고 복원)
_ORIG_VALIDATE = server._validate_api_key
_ORIG_SAVE = server._save_api_key_to_env


async def call_login(api_key: str) -> str:
    res = await server.call_tool("waple_login", {"api_key": api_key})
    return res[0].text


def make_case(exc: Exception):
    """검증 함수가 지정한 네트워크 예외를 던지도록 하고,
    저장 함수가 불렸는지 추적하는 스파이를 설치한다."""
    saved = []  # _save_api_key_to_env가 불리면 여기에 기록됨

    def fake_validate(api_key):
        raise exc

    def spy_save(api_key):
        saved.append(api_key)

    server._validate_api_key = fake_validate
    server._save_api_key_to_env = spy_save
    return saved


async def case_timeout():
    saved = make_case(requests.exceptions.Timeout("타임아웃(테스트)"))
    txt = await call_login("test-key-abcd")

    checks = {
        "실패 보고(❌)": "❌" in txt,
        "재시도 방법 안내(다시 실행)": "다시 실행" in txt,
        "같은 키 안내": "같은 API 키" in txt,
        "재시도 안전성 안내(저장 안 됨)": "저장" in txt,
        "키 저장 안 됨(_save 미호출)": saved == [],
    }
    return all(checks.values()), {**checks, "응답": txt.strip().replace("\n", " ")[:60]}


async def case_connection():
    saved = make_case(requests.exceptions.ConnectionError("연결오류(테스트)"))
    txt = await call_login("test-key-abcd")

    checks = {
        "실패 보고(❌)": "❌" in txt,
        "재시도 방법 안내(다시 실행)": "다시 실행" in txt,
        "같은 키 안내": "같은 API 키" in txt,
        "재시도 안전성 안내(저장 안 됨)": "저장" in txt,
        "키 저장 안 됨(_save 미호출)": saved == [],
    }
    return all(checks.values()), {**checks, "응답": txt.strip().replace("\n", " ")[:60]}


async def main():
    print("=" * 54)
    print(" waple_login 재시도 안내(UX ②) 검증 — 실서버 요청 없음")
    print("=" * 54)

    all_pass = True

    p1, d1 = await case_timeout()
    all_pass = all_pass and p1
    print(f"\n[{'PASS ✅' if p1 else 'FAIL ❌'}] 타임아웃 → 재시도 안내")
    for k, v in d1.items():
        print(f"      - {k}: {v}")

    p2, d2 = await case_connection()
    all_pass = all_pass and p2
    print(f"\n[{'PASS ✅' if p2 else 'FAIL ❌'}] 연결오류 → 재시도 안내")
    for k, v in d2.items():
        print(f"      - {k}: {v}")

    # 테스트 격리: 원본 복원
    server._validate_api_key = _ORIG_VALIDATE
    server._save_api_key_to_env = _ORIG_SAVE

    print("\n" + "=" * 54)
    print(" 전체 결과:", "모두 통과 ✅" if all_pass else "실패 항목 있음 ❌")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())