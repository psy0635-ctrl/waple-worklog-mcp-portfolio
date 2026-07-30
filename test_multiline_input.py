"""
여러 줄 입력 정규화 테스트 (_format_bullet_lines).

배경 (7/30):
    tomorrow_plan / memo / activities / created_files에 개행이 포함된 값이 들어오면
    "- "와 근거 라벨이 첫 줄 또는 마지막 줄에만 붙고 중간 줄은 맨몸으로 남았다.
    tomorrow_plan은 서식만 깨지지만, memo·activities는 근거 라벨이 사라진 줄이
    'Git에서 확인된 사실'처럼 보이게 되어 지침 6번을 위반한다.

네트워크·실서버 접근 없음. 문자열 조립 함수만 검증한다.
"""
import server


# ────────────────────────────────────────────────
# 1. 헬퍼 자체 동작
# ────────────────────────────────────────────────

def test_helper_splits_every_line():
    """여러 줄 입력의 모든 줄에 "- "가 붙어야 한다"""
    result = server._format_bullet_lines("첫째 줄\n둘째 줄\n셋째 줄")
    assert result == ["- 첫째 줄", "- 둘째 줄", "- 셋째 줄"]


def test_helper_does_not_duplicate_existing_bullet():
    """사용자가 이미 붙인 불릿 기호가 "- - 내용"으로 겹치면 안 된다"""
    result = server._format_bullet_lines("- 하이픈 선입력\n• 점 기호\n* 별표")
    assert result == ["- 하이픈 선입력", "- 점 기호", "- 별표"]


def test_helper_drops_blank_and_symbol_only_lines():
    """빈 줄과 기호만 있는 줄은 버린다 (본문 중간 공백은 구분선 구조를 깨뜨림)"""
    result = server._format_bullet_lines("내용 있음\n\n   \n-\n•\n마지막 줄")
    assert result == ["- 내용 있음", "- 마지막 줄"]


def test_helper_preserves_hyphen_inside_word():
    """기호 뒤에 공백이 없으면 불릿이 아니라 내용의 일부로 본다"""
    result = server._format_bullet_lines("-와 관련된 작업\n2026-07-30 회의")
    assert result == ["- -와 관련된 작업", "- 2026-07-30 회의"]


def test_helper_labels_every_line():
    """근거 라벨은 줄마다 붙어야 한다 (각 줄이 독립된 업무 항목이므로)"""
    result = server._format_bullet_lines("A\nB", label="[사용자 메모]")
    assert result == ["- A [사용자 메모]", "- B [사용자 메모]"]


def test_helper_applies_prefix_to_every_line():
    """고정 접두어도 줄마다 붙어야 한다"""
    result = server._format_bullet_lines(
        "server.py\ntest_connector.py", label="[생성 파일]", prefix="생성/수정 파일: "
    )
    assert result == [
        "- 생성/수정 파일: server.py [생성 파일]",
        "- 생성/수정 파일: test_connector.py [생성 파일]",
    ]


def test_helper_returns_empty_list_for_blank_input():
    """빈 입력은 빈 리스트 — 대체 문구는 호출부가 결정한다"""
    assert server._format_bullet_lines("") == []
    assert server._format_bullet_lines(None) == []
    assert server._format_bullet_lines("   \n\n  ") == []


# ────────────────────────────────────────────────
# 2. tasklog 경로 (_build_draft)
# ────────────────────────────────────────────────

STATUS_EMPTY = {"staged": [], "unstaged": []}


def test_build_draft_multiline_tomorrow_plan():
    """[재현 버그] 내일 계획 3줄이 모두 불릿으로 나와야 한다"""
    _, memo = server._build_draft(
        "2026-07-30", [], STATUS_EMPTY, [], "",
        "계획 A\n계획 B\n계획 C", None,
    )
    for line in ("- 계획 A", "- 계획 B", "- 계획 C"):
        assert line in memo
    # 라벨 없는 맨몸 줄이 남아 있으면 안 됨
    assert "\n계획 B" not in memo


def test_build_draft_multiline_memo_keeps_label_on_all_lines():
    """[재현 버그 - 가장 위험] 자기 보고 라벨이 일부 줄에서 사라지면 안 된다"""
    _, memo = server._build_draft(
        "2026-07-30", [], STATUS_EMPTY, [], "오전 회의 참석\n오후 코드 리뷰", "", None,
    )
    assert "- 오전 회의 참석 [사용자 메모]" in memo
    assert "- 오후 코드 리뷰 [사용자 메모]" in memo


def test_build_draft_empty_plan_still_shows_placeholder():
    """빈 계획은 기존과 동일하게 "(미기재)" (회귀 방지)"""
    _, memo = server._build_draft("2026-07-30", [], STATUS_EMPTY, [], "", "", None)
    assert "(미기재)" in memo


# ────────────────────────────────────────────────
# 3. chat_tasklog 경로 (_build_chat_draft)
# ────────────────────────────────────────────────

def test_build_chat_draft_multiline_activity_keeps_label():
    """activities 항목 안에 개행이 있어도 [대화 기반] 라벨이 줄마다 유지돼야 한다"""
    _, memo = server._build_chat_draft(
        "2026-07-30", ["활동 첫 줄\n활동 둘째 줄"], [], "", "", [],
    )
    assert "- 활동 첫 줄 [대화 기반]" in memo
    assert "- 활동 둘째 줄 [대화 기반]" in memo


def test_build_chat_draft_multiline_created_files():
    """created_files 항목 안에 개행이 있어도 접두어·라벨이 줄마다 유지돼야 한다"""
    _, memo = server._build_chat_draft(
        "2026-07-30", [], ["server.py\ntest_connector.py"], "", "", [],
    )
    assert "- 생성/수정 파일: server.py [생성 파일]" in memo
    assert "- 생성/수정 파일: test_connector.py [생성 파일]" in memo


# ────────────────────────────────────────────────
# 4. 두 경로의 처리 일치 고정
#    (7/29 _HEADER_AUTH_GUIDE 건과 같은 취지 —
#     같은 로직이 두 함수에 복붙되어 한쪽만 고쳐지는 재발을 막는다)
# ────────────────────────────────────────────────

def test_both_builders_format_plan_identically():
    """같은 tomorrow_plan을 넣으면 두 함수의 '내일(향후) 계획' 블록이 동일해야 한다"""
    plan = "- 선입력 하이픈\n두 번째 줄\n\n세 번째 줄"

    def plan_block(text):
        # "내일(향후) 계획" 헤더 이후 구간만 잘라낸다
        return text.split("내일(향후) 계획", 1)[1]

    _, code_memo = server._build_draft(
        "2026-07-30", [], STATUS_EMPTY, [], "", plan, None
    )
    _, chat_memo = server._build_chat_draft(
        "2026-07-30", [], [], "", plan, []
    )
    assert plan_block(code_memo) == plan_block(chat_memo)
