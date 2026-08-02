# scripts — 문서 빌드

`docs/waple-worklog-mcp-상세설명.md` 를 PDF로 변환하는 스크립트입니다.

## 실행

레포 루트에서 실행합니다.

```bash
python scripts/build_pdf.py
```

리눅스에서는 시스템 파이썬이 아니라 위에서 만든 venv 파이썬을 지정합니다.

```bash
~/.venv-pdf/bin/python scripts/build_pdf.py
```

| 구분 | 경로 |
| --- | --- |
| 입력 | `docs/waple-worklog-mcp-상세설명.md` |
| 출력 | `docs/waple-worklog-mcp-상세설명.pdf` (덮어씀) |
| 서식 | `scripts/pdf_style.css` |

서식만 바꾸고 싶을 때는 `pdf_style.css` 만 수정하면 됩니다.

## 실행 환경

**데비안·우분투 계열 리눅스에서만 동일한 결과가 나옵니다.**
아래 폰트가 시스템에 설치되어 있어야 하며, Windows에는 없는 폰트가
포함되어 있어 Windows 로컬 실행 시 결과물이 달라집니다.

### 파이썬 패키지

Ubuntu 24.04 는 시스템 파이썬에 직접 설치하는 것을 막아 두었습니다(PEP 668).
venv 를 새로 만들어 그 안에 설치합니다. 레포 안의 `.venv` 는 Windows 용
(`Scripts/`)이므로 섞이지 않도록 레포 밖(홈 디렉터리)에 따로 둡니다.

```bash
python3 -m venv ~/.venv-pdf
~/.venv-pdf/bin/pip install weasyprint markdown
```

### 폰트

```bash
sudo apt-get install -y fonts-noto-cjk fonts-dejavu-core \
  fonts-noto-color-emoji poppler-utils python3-venv
```

| 용도 | 폰트 |
| --- | --- |
| 한글 본문 | Noto Sans CJK KR |
| 코드 (영문) | DejaVu Sans Mono |
| 코드 (한글) | Noto Sans Mono CJK KR |
| 이모지 | Noto Color Emoji |

### 검증 도구 (필수)

```bash
sudo apt-get install -y poppler-utils
```

`pdftotext` 가 있으면 스크립트가 생성 직후 내용 검증까지 수행합니다.
없으면 검증할 수 없으므로 경고만 출력하고 오류로 중단합니다(종료 코드 1).
검사를 건너뛴 경우와 검사를 통과한 경우가 같은 종료 코드이면 안 되기
때문입니다.

## 주의

**폰트가 없어도 스크립트는 오류 없이 정상 종료됩니다.**
이 경우 PDF는 만들어지지만 한글이 통째로 빠지거나 네모(□)로 표시됩니다.
그래서 `build_pdf.py` 는 생성 후 아래 두 가지를 자동으로 확인합니다.

1. 본문에서 한글 문자열이 실제로 추출되는지 (폰트 누락 감지)
2. 서버 주소·계정·포트로 보이는 값이 섞이지 않았는지

두 번째 검사는 금지할 값을 코드에 적어두지 않고 **형태(패턴)로** 찾습니다.
막아야 할 값을 그대로 적으면 공개 레포에서는 그 코드가 값을 노출하게 되기
때문입니다. 문서에 정상적으로 등장하는 주소(`127.0.0.1`, `0.0.0.0` 등)와
일반적인 개발 포트는 `scripts/check_secrets.py` 의 예외 목록에 두었습니다.
예외는 두 층위입니다. 공개해도 되는 **값**을 지정하는 목록(`ALLOWED_` 로
시작하는 상수들)과, 예시값이 본문이라 **파일 전체**를 검사에서 빼는
`SELF_EXEMPT` 입니다. 오탐이 나면 해당하는 쪽에 추가하시면 됩니다.
실제 목록은 오탐 시 출력되는 안내 메시지에 그대로 나옵니다.

둘 중 하나라도 걸리면 오류 메시지를 내고 중단합니다.

다만 **표가 페이지 폭을 넘어 잘리는 것은 자동으로 잡히지 않습니다.**
문서를 크게 고친 뒤에는 해당 페이지를 눈으로 확인하시기 바랍니다.

```bash
pdftoppm -png -r 110 -f 8 -l 8 docs/waple-worklog-mcp-상세설명.pdf page8
```

`-f` 는 시작 페이지, `-l` 은 끝 페이지입니다.

- 파이썬 스크립트에 `/c/study/...` 형태의 Git Bash 경로를 넘기지 마십시오. Windows 프로그램이라 `C:\c\study\` 로 해석됩니다. 상대경로를 쓰십시오.
- 실행 전 `python -c "import sys; print(sys.executable)"` 로 어느 venv인지 확인하십시오.

## 정보 노출 검사 (check_secrets.py)

PDF 생성과 별개로, 커밋 전에 바로 돌릴 수 있는 검사 스크립트입니다.

```bash
python scripts/check_secrets.py <파일...>
python scripts/check_secrets.py --all
python scripts/check_secrets.py --all --quiet
```

통과하면 종료 코드 0, 발견되면 1을 돌려줍니다. 아래처럼 이어 붙이면
검사를 통과할 때만 커밋됩니다.

```bash
python scripts/check_secrets.py --all && git commit -m "..."
```

막아야 할 값(서버 IP·도메인 등)을 코드에 그대로 적지 않고 형태(패턴)로
찾습니다. 값을 코드에 적는 순간 그 코드 자체가 값을 노출하게 되기
때문입니다.

공개해도 되는 값은 `ALLOWED_HOSTS` / `ALLOWED_IP_PREFIXES` /
`ALLOWED_PORTS` / `ALLOWED_DOMAINS` / `ALLOWED_EMAILS` 로 관리합니다
(`ALLOWED_EMAILS` 는 현재 빈 목록입니다). 예시값이 본문인 파일 자체를
검사에서 빼는 것은 `SELF_EXEMPT` 로 관리합니다.

한계: 도메인 검사는 TLD 목록 기반이라 목록에 없는 TLD는 통과합니다.

## 참고 — 이 스크립트를 만든 이유

이전까지 이 PDF는 일회성 작업 환경에서 생성되어 변환 명령과 CSS가
레포에 남지 않았습니다. 그 결과 문서를 고칠 때마다 변환 방법을 다시
찾아야 했고, 실제로 2026-07-30 작업 중 원본 CSS를 복원하지 못해
서식을 다시 정의해야 했습니다.

산출물만 있고 만드는 방법이 없으면 재현 가능한 결과물이 아니라고 보아,
변환 절차 자체를 레포에 포함시켰습니다.