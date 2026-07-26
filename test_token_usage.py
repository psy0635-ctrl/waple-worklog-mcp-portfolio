# -*- coding: utf-8 -*-
"""
멘토 피드백 ① (7/10) — '오늘 사용한 토큰' 수집 검증

목적:
  _collect_today_token_usage가 Claude Code 세션 로그(jsonl)에서
  ① 오늘(로컬 날짜) 기록만 합산하고
  ② 같은 message id의 중복 레코드를 이중 합산하지 않고(마지막 값 채택)
  ③ 깨진 줄·usage 없는 줄을 안전하게 건너뛰고
  ④ 로그가 없으면 None(수집 불가)을 반환하는지 확인한다.
  실제 ~/.claude 폴더는 건드리지 않는다(임시 폴더 + monkeypatch).

실행 위치:
  server.py와 같은 폴더(llm팀/)에서 실행한다.
      cd C:\\study\\uxis\\2026-uxis-mirae\\llm팀
      python test_token_usage.py

기대 출력:
  네 케이스가 모두 [PASS ✅]면 정상이다.
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import server

# 원본 보존 (테스트 격리: 끝나고 복원)
# [PR #12 (7/15)] 함수가 단수 → 복수(_get_claude_projects_log_dirs, 리스트 반환)로 바뀜
_ORIG_GET_LOG_DIRS = server._get_claude_projects_log_dirs


def _ts(dt: datetime) -> str:
    """로그 timestamp 형식(UTC, Z 접미사)으로 변환."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _rec(msg_id: str, when: datetime, inp: int, out: int, cc: int = 0, cr: int = 0) -> str:
    """usage가 포함된 로그 레코드 한 줄(JSON 문자열)을 만든다."""
    return json.dumps({
        "timestamp": _ts(when),
        "message": {
            "id": msg_id,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cc,
                "cache_read_input_tokens": cr,
            },
        },
    })


def _use_fake_log_dir(tmp: Path):
    """_get_claude_projects_log_dirs가 임시 폴더 '리스트'를 반환하도록 교체."""
    # [PR #12 (7/15)] 반환 타입이 list[Path]이므로 임시 폴더를 리스트로 감싼다.
    server._get_claude_projects_log_dirs = lambda: [tmp]


# ============================================================
# 케이스 1 — 오늘 기록만 합산 (어제 기록 제외)
# ============================================================
def case_1():
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1, hours=2)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "s1.jsonl").write_text(
            "\n".join([
                _rec("m1", now, inp=100, out=10, cr=1000),   # 오늘 → 포함
                _rec("m2", now, inp=200, out=20, cc=500),    # 오늘 → 포함
                _rec("m3", yesterday, inp=999, out=999),      # 어제 → 제외
            ]) + "\n",
            encoding="utf-8",
        )
        _use_fake_log_dir(tmp)
        r = server._collect_today_token_usage()

    checks = {
        "합산 결과 존재": r is not None,
        "입력 = 300 (어제 999 제외)": r and r["input"] == 300,
        "출력 = 30": r and r["output"] == 30,
        "캐시 = 1500": r and r["cache"] == 1500,
        "총합 = 1830": r and r["total"] == 1830,
        "메시지 수 = 2": r and r["messages"] == 2,
    }
    return all(checks.values()), {**checks, "결과": r}


# ============================================================
# 케이스 2 — 같은 id 중복 레코드 → 이중 합산 금지 (마지막 값 채택)
#   (실측 7/10: 완전 동일 레코드가 2줄 연속 기록되는 사례 확인됨)
# ============================================================
def case_2():
    now = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "s1.jsonl").write_text(
            "\n".join([
                _rec("dup", now, inp=100, out=10),   # 1차 기록
                _rec("dup", now, inp=100, out=50),   # 같은 id 갱신(스트리밍) → 이 값 채택
                _rec("solo", now, inp=1, out=1),
            ]) + "\n",
            encoding="utf-8",
        )
        _use_fake_log_dir(tmp)
        r = server._collect_today_token_usage()

    checks = {
        "메시지 수 = 2 (dup는 1건)": r and r["messages"] == 2,
        "입력 = 101 (100+100 이중합산 아님)": r and r["input"] == 101,
        "출력 = 51 (마지막 값 50 채택)": r and r["output"] == 51,
    }
    return all(checks.values()), {**checks, "결과": r}


# ============================================================
# 케이스 3 — 깨진 줄·usage 없는 줄 안전 통과
# ============================================================
def case_3():
    now = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "s1.jsonl").write_text(
            "\n".join([
                "이건 JSON이 아님 {{{",                                  # 깨진 줄 → 스킵
                json.dumps({"timestamp": _ts(now), "message": {"id": "x"}}),  # usage 없음 → 스킵
                _rec("ok", now, inp=42, out=8),                          # 정상 → 포함
            ]) + "\n",
            encoding="utf-8",
        )
        _use_fake_log_dir(tmp)
        r = server._collect_today_token_usage()

    checks = {
        "에러 없이 결과 반환": r is not None,
        "정상 레코드만 합산 (입력 42)": r and r["input"] == 42,
        "메시지 수 = 1": r and r["messages"] == 1,
    }
    return all(checks.values()), {**checks, "결과": r}


# ============================================================
# 케이스 4 — 수집 불가 경로: 로그 폴더 없음 / 오늘 기록 없음 → None
#            + 초안에 '수집 불가' 문구가 들어가는지
# ============================================================
def case_4():
    # (a) 로그 폴더 자체가 없음
    # [PR #12 (7/15)] '폴더 없음'은 이제 빈 리스트([])로 표현한다.
    server._get_claude_projects_log_dirs = lambda: []
    r_none = server._collect_today_token_usage()

    # (b) 폴더는 있으나 오늘 기록 없음
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=2)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "s1.jsonl").write_text(_rec("old", yesterday, 1, 1) + "\n", encoding="utf-8")
        _use_fake_log_dir(tmp)
        r_empty = server._collect_today_token_usage()

    # (c) 초안 문구 확인: token_usage 유/무 각각
    today = server.date.today().isoformat()
    empty_status = {"staged": [], "unstaged": []}
    _, memo_with = server._build_draft(
        today, [], empty_status, [], "", "",
        {"input": 300, "output": 30, "cache": 1500, "total": 1830, "messages": 2},
    )
    _, memo_without = server._build_draft(today, [], empty_status, [], "", "", None)

    checks = {
        "폴더 없음 → None": r_none is None,
        "오늘 기록 없음 → None": r_empty is None,
        "초안에 토큰 줄 포함": "오늘 사용한 토큰: 총 1,830" in memo_with,
        "초안에 근거 표시 포함": "[Claude Code 세션 로그]" in memo_with,
        "수집 불가 시 정직 표기": "수집 불가" in memo_without,
    }
    return all(checks.values()), checks


# ============================================================
# 실행
# ============================================================
def main():
    print("=" * 54)
    print(" 토큰 사용량 수집 검증 — 실제 ~/.claude 미접근")
    print("=" * 54)

    cases = [
        ("1. 오늘 기록만 합산 (어제 제외)", case_1),
        ("2. 같은 id 중복 → 이중 합산 금지", case_2),
        ("3. 깨진 줄·usage 없는 줄 안전 통과", case_3),
        ("4. 수집 불가 → None + 초안 문구", case_4),
    ]

    all_pass = True
    for title, fn in cases:
        passed, detail = fn()
        all_pass = all_pass and passed
        print(f"\n[{'PASS ✅' if passed else 'FAIL ❌'}] {title}")
        for k, v in detail.items():
            print(f"      - {k}: {v}")

    # 테스트 격리: 원본 복원
    # [PR #12 (7/15)] 복원 대상도 복수형 함수로 변경
    server._get_claude_projects_log_dirs = _ORIG_GET_LOG_DIRS

    print("\n" + "=" * 54)
    print(" 전체 결과:", "모두 통과 ✅" if all_pass else "실패 항목 있음 ❌")
    print("=" * 54)


if __name__ == "__main__":
    main()