# repro_tomorrow_plan.py
# 목적: tomorrow_plan / memo 여러 줄 입력 시 첫 줄에만 "- "가 붙는지 확인
# 순수 문자열 조립 함수만 호출하므로 Git·Waple·네트워크 접근 없음
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")  # 콘솔 코드페이지(cp949)와 무관하게 UTF-8 출력

from server import _build_draft, _build_chat_draft

PLAN = "tomorrow_plan 줄바꿈 버그 수정\n_format_bullet_lines 헬퍼 설계\n테스트 4케이스 추가"
MEMO = "오전 팀 회의 참석\n오후 코드 리뷰 대응"
TODAY = "2026-07-30"
STATUS = {"staged": [], "unstaged": []}          # _collect_git_status 반환 형태와 동일

def show(title, text):
    print("=" * 60)
    print(title)
    print("=" * 60)
    print(text)
    print()

# [1] Claude Code 경로 — 인계 메모에 없던 지점
_, sub = _build_draft(TODAY, [], STATUS, [], "", PLAN, None)
show("[1] tasklog / _build_draft — tomorrow_plan 3줄", sub)

# [2] 채팅 경로 — 이미 재현 확인된 지점 (대조군)
_, sub = _build_chat_draft(TODAY, [], [], "", PLAN, [])
show("[2] chat_tasklog / _build_chat_draft — tomorrow_plan 3줄", sub)

# [3] 같은 패턴이 memo에도 있는지
_, sub = _build_draft(TODAY, [], STATUS, [], MEMO, "", None)
show("[3] _build_draft — memo 2줄", sub)