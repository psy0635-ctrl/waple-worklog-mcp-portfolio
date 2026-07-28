# Waple 업무일지 MCP 서버

개발자가 하루 동안 수행한 작업을 **Git 기록과 Claude Code 세션 로그에서 자동으로 수집**하여
업무일지 초안을 만들고, **사용자가 승인한 내용만** HR SaaS 플랫폼(Waple)에 등록하는 MCP 서버입니다.

8주 인턴 팀 프로젝트(4인) 중 **MCP 서버 파트를 담당**하여 개발하였습니다.
코드 리뷰는 팀원이 수행하였으며, PR #13에서 지적받은 인증 처리 결함을 반영해 구조를 수정하였습니다.

---

## 1. 왜 만들었는가

업무일지는 매일 써야 하지만, 하루가 끝날 무렵에는 무엇을 했는지 정확히 기억나지 않습니다.
그 결과 "개발 진행함" 같은 내용 없는 기록이 쌓이고, 인사 데이터로서의 가치를 잃습니다.

이미 커밋 메시지와 변경 파일에 하루의 기록이 남아 있는데도 다시 손으로 쓰고 있다는 점에 주목하였습니다.
그래서 **이미 존재하는 근거를 모아 초안을 만들고, 사람은 확인만 하는** 방향으로 설계하였습니다.

---

## 2. 시연

**로컬 시연 (3분 51초)** — 초안 생성부터 승인·등록까지 전체 흐름

https://github.com/user-attachments/assets/11a2e83e-085b-435b-8d98-cd3bad4a6dc9

**등록 결과 (1분 43초)** — Waple 웹 화면에서 등록된 업무일지 확인

https://github.com/user-attachments/assets/4606369c-0ba7-4db5-b42f-f2e1ab72ff88

### 실제 생성된 초안 (2026-07-25)

```
오늘 한 일
- test_connector.py 의 import 문을 파일 상단으로 정리하였습니다. [커밋 97a822d9]
- 포트폴리오용 시연 자료를 촬영하였습니다. [사용자 메모]
- 오늘 사용한 토큰: 세션 로그에서 자동 집계[Claude Code 세션 로그]
```

각 항목 끝의 대괄호가 **작성 근거**입니다.
커밋에서 나온 내용인지, 사용자가 직접 적은 메모인지, 세션 로그에서 집계한 값인지 구분됩니다.
근거가 없는 내용은 지어내지 않고 "사용자 확인 필요" 항목으로 분리합니다.

---

## 3. 동작 구조

```
[Claude Code / Claude 데스크톱 앱]
            │  MCP 프로토콜
            ▼
    ┌───────────────────┐
    │   MCP 서버        │
    │  (server.py)      │
    ├───────────────────┤
    │ 근거 수집          │ ← git log / git diff / 세션 로그(JSONL)
    │ 업무 단위 분류     │
    │ 초안 생성          │
    │ 승인 확인          │ ← 여기서 멈춤. 승인 전에는 등록하지 않음
    │ Waple API 호출     │ ──▶ POST /api/mcp/diary
    └───────────────────┘
```

### 이중 transport 구조

| 방식 | 용도 | 인증 |
| --- | --- | --- |
| **stdio** | 로컬 Claude Code | `.env` 파일의 키 |
| **Streamable HTTP** | 원격 접속 (nginx + HTTPS) | 요청 헤더 `x-api-key` |

HTTP 하나로 통일하지 못한 이유가 있습니다.
원격 서버는 사용자 PC의 Claude Code 세션 로그(`~/.claude/projects`)에 접근할 수 없어
**토큰 사용량 집계가 불가능**합니다. 기능을 유지하려면 두 경로가 모두 필요하였습니다.

---

## 4. 제공 도구

| 도구 | 역할 |
| --- | --- |
| `waple_login` | API 키 검증 |
| `tasklog` | Git 기록 기반 초안 생성 (로컬 전용) |
| `chat_tasklog` | 대화 내용 기반 초안 생성 (원격 지원) |
| `submit_worklog` | **승인된 초안만** Waple에 등록 |

초안 생성과 등록을 **의도적으로 다른 도구로 분리**하였습니다.
하나로 합치면 "정리해줘"라는 말에도 등록이 실행될 수 있기 때문입니다.

---

## 5. 설계 시 신경 쓴 부분

### 승인 없이는 등록하지 않는다

`정리해줘`, `초안 만들어줘`, `검토해줘` 는 승인으로 보지 않습니다.
`등록해`, `승인`, `이 내용으로 올려` 처럼 명확한 표현이 있을 때만 API를 호출합니다.

### 요청별 키 격리

원격 모드에서는 여러 사용자가 같은 서버 프로세스를 공유합니다.
`ContextVar` 로 요청마다 키를 분리하고, 초안 캐시도 키의 해시값으로 사용자별 스코핑하여
**다른 사용자의 초안이 섞이지 않도록** 하였습니다.

### HTTP 모드에서는 키를 저장하지 않는다

`.env` 저장 로직은 stdio 모드에서만 동작합니다.
원격 사용자의 키가 서버 파일에 남는 것을 막기 위함입니다.

---

## 6. 빠른 시작

```bash
git clone https://github.com/psy0635-ctrl/waple-worklog-mcp-portfolio.git
cd waple-worklog-mcp-portfolio

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # 실제 값을 채워 넣습니다
```

**로컬 실행 (Claude Code)**

```bash
cp .mcp.json.example .mcp.json
claude                           # 실행 후 /mcp 로 연결 확인
```

**원격 실행 (Streamable HTTP)**

```bash
python server.py --transport streamable-http --port 8010
```

```bash
claude mcp add --transport http waple-remote \
  https://[SERVER_URL]/llm/mcp --header "x-api-key: 발급받은키"
```

자세한 배포·연동 절차는 [docs/integration-manual.md](docs/integration-manual.md) 를 참고하십시오.

---

## 7. 테스트

```bash
python -m pytest -q
```

```
22 passed, 1 skipped
```

`skipped` 1건은 로그 폴더 대소문자 혼재 케이스입니다.
Windows 파일시스템에서는 대소문자만 다른 폴더를 동시에 만들 수 없어 건너뛰고,
리눅스에서 별도로 실행해 통과를 확인하였습니다.

| 파일 | 검증 내용 |
| --- | --- |
| `test_connector.py` | 원격 커넥터, 사용자별 초안 스코핑 |
| `test_cache.py` | 초안 캐시 동작 |
| `test_guideline17.py` | 승인 절차·필수값 누락·중복 등록 방지 |
| `test_login_retry.py` | 인증 실패 시 재시도 안내 |
| `test_token_usage.py` | 세션 로그 기반 토큰 집계 |
| `test_draft_cleanup.py` | 초안 캐시 누적 방지 (등록 후 삭제, TTL 정리) |

---

## 8. 검증 현황

사실과 추정을 구분하기 위해 항목마다 검증 수준을 표기하였습니다.

| 항목 | 결과 | 검증 수준 |
| --- | --- | --- |
| 로컬(stdio) 전체 흐름 | 초안 생성 → 승인 → Waple 등록 성공 | 실물 검증 |
| 원격(HTTP) 연동 | Claude Code 에서 호출, 서버 로그에 기록 확인 | 실물 검증 |
| 자동 재시작 | 프로세스 강제 종료 후 PID 변경 확인 | 실물 검증 |
| 재부팅 후 자동 기동 | `systemctl --user is-enabled`, `Linger=yes` 확인 | 설정 확인 (재부팅 미실시) |
| claude.ai 웹 커넥터 | 연결·도구 목록·도구 호출은 성공, 인증은 불가 (헤더 지정 수단 없음) | 실물 검증 (직접 호출 + 설정 화면 확인) |

웹 커넥터가 되지 않는 이유는 추정이 아니라 서버 로그로 확인하였습니다.
5일간(2026.07.21 ~ 07.25) 누적된 요청을 발신 경로별로 대조한 결과입니다.

| 발신 경로 | 도구 목록 조회 | 도구 호출 |
| --- | --- | --- |
| claude.ai 웹 커넥터 | 19건 | **0건** |
| 설정 파일 기반 클라이언트 (Claude Code, 데스크톱 앱) | 26건 | 12건 |

웹 커넥터 쪽 응답 코드는 200과 202뿐이고 4xx·5xx는 없었습니다.
연결·TLS·프록시·목록 조회까지는 정상이었고, 이 기간에는 도구 호출이 성립하지 않았습니다.

이후 2026-07-28 에 **웹 커넥터로 도구를 직접 호출해 서버 응답을 받았습니다.**
관찰에서 추론하던 원인을 실물로 확정한 것입니다.

| 시점 | 도구 목록 조회 | 도구 호출 | 인증 |
| --- | --- | --- | --- |
| 2026.07.21 ~ 07.25 | 19건 | 0건 | (판단 불가) |
| 2026.07.28 | 정상 (4개 인식) | **성공** | **실패** |

07.28 호출 시 서버가 반환한 것은 "요청에 `x-api-key` 헤더가 없다"는 안내였습니다.
즉 **연결과 도구 호출은 되지만 인증 수단이 없어 실사용이 불가능한 상태**입니다.

claude.ai 웹 커넥터의 설정 화면에서 제공되는 인증 항목은 고급 설정을 펼쳐도
OAuth 클라이언트 ID·시크릿 두 가지뿐이며, 임의의 요청 헤더를 지정하는 입력란은
제공되지 않습니다(2026-07-28 화면 확인). 이 서버는 `x-api-key` 헤더로 인증하므로
웹 커넥터 경로로는 인증이 성립하지 않습니다.

설정 파일 기반 클라이언트(Claude Code `--header`, 데스크톱 앱 `mcp-remote` 브리지)는
헤더를 지정할 수 있어 정상 동작합니다.

> **검토했으나 원인이 아니었던 것:** FastMCP 의 DNS Rebinding 방어가 Origin 헤더를
> 검증해 `https://claude.ai` 를 403 으로 거부할 가능성을 확인하였습니다. 그러나 위
> 로그에서 웹 커넥터 요청의 응답 코드는 200·202 뿐이고 403 이 한 건도 없었으므로,
> Origin 차단은 실제로 발생하지 않았습니다. 현재 허용 목록에 해당 Origin 이
> 포함되어 있으나 이는 버그 수정이 아니라 예방적 조치입니다.

마스킹한 로그 발췌는 [docs/evidence/webconnector_evidence_masked.txt](docs/evidence/webconnector_evidence_masked.txt) 에 있습니다.

---

## 9. 막혔던 부분

| 문제 | 원인 | 해결 |
| --- | --- | --- |
| 원격 접속 시 421 반환 | FastMCP 의 DNS Rebinding 방지로 허용 Host 가 `127.0.0.1` 로 잠김 | 우선 nginx 에서 Host 헤더를 재작성해 우회. 이후 `transport_security` 로 허용 Host·Origin 을 코드에 명시 (아래 참고) |
| 커넥터 연결 실패 | HTTPS 인증서 체인에 중간 인증서 누락 | 브라우저는 자동 보완되지만 서버 간 통신은 실패함을 확인, fullchain 적용 |
| 서버 재부팅 후 중단 | `nohup` 프로세스가 재부팅과 함께 소멸 | sudo 권한이 제한된 환경이라 사용자 systemd + linger 로 해결 |
| API 키 터미널 노출 | 키 확인 과정에서 값이 평문 출력됨 | 키 삭제 후 재발급. 이후 키를 다루는 명령은 값이 표시되지 않는 방식으로 변경 |

### 프로젝트 종료 후 개선 (2026-07-28)

421 은 nginx 우회로 이미 해결된 상태였지만, 근본 원인을 다시 확인한 결과
**SDK 가 방어를 켜주는 조건이 실행 옵션에 달려 있다**는 점을 발견하였습니다.

FastMCP 는 생성자의 `host` 가 `127.0.0.1`/`localhost`/`::1` 일 때만 DNS Rebinding
방어를 자동 활성화합니다. 그런데 이 서버의 실행 인자 도움말은 "외부 공개 배포 시
0.0.0.0" 을 안내하고 있었습니다. 안내대로 실행하면 보안 기능이 경고 한 줄 없이
꺼지는 구조였습니다.

허용 Host·Origin 을 코드에 직접 명시해 실행 옵션·SDK 버전과 무관하게 동일한 설정이
적용되도록 수정하였습니다. 배포 서버에서 적용 전후를 측정한 결과입니다.

| 측정 조건 | before | after |
| --- | --- | --- |
| 정상 요청 | 406 | 406 |
| 위조 Host | 421 | 421 (방어 유지) |
| 도메인 Host | 421 | 406 |
| `Origin: https://claude.ai` | 403 | 406 |
| 외부 HTTPS 경유 | 406 | 406 |

허용 목록을 SDK 자동 기본값의 상위집합으로 구성하여, 기존에 동작하던 경로가
막히지 않도록 하였습니다. 적용 후 MCP 핸드셰이크와 도구 호출까지 실측하였습니다.
nginx 의 Host 재작성은 이중 안전망으로 그대로 유지하였습니다.

---

마지막 항목이 가장 뼈아팠습니다.
키를 재발급하기만 해서는 기존 키가 살아 있다는 점을 뒤늦게 알았고,
**반드시 기존 키를 삭제해야 무효화된다**는 것을 확인하였습니다.

각 사례의 판단 과정은 [docs/development-notes.md](docs/development-notes.md) 에 정리해 두었습니다.

---

## 10. 기술 스택

Python · MCP Python SDK (FastMCP) · requests · pytest
nginx (리버스 프록시, HTTPS) · systemd (사용자 서비스)

---

## 11. 문서

| 문서 | 내용 |
| --- | --- |
| [docs/waple-worklog-mcp-상세설명.pdf](docs/waple-worklog-mcp-상세설명.pdf) | 프로젝트 전체 과정 상세 설명 (인쇄용) |
| [docs/integration-manual.md](docs/integration-manual.md) | 설치·배포·연동 절차 |
| [docs/connector-design.md](docs/connector-design.md) | 근거 수집 설계 |
| [docs/development-notes.md](docs/development-notes.md) | 개발 기록, 오류 사례, 설계 편차와 근거 |
| [deploy/README.md](deploy/README.md) | systemd 운영 가이드 |
| [docs/evidence/](docs/evidence/) | 웹 커넥터 검증에 사용한 서버 로그 발췌 (마스킹) |

---

## 12. 남은 과제

- 원격 모드에서 `tasklog` 호출 시, 사용자 PC 가 아니라 **배포 서버 저장소의 Git
  기록이 수집**되는 문제 (2026-07-28 실측). 원격에서는 `chat_tasklog` 사용을
  전제하고 있으나, HTTP 모드에서 `tasklog` 를 차단하거나 명시적으로 안내해야 합니다
- 원격 커넥터 인증 실패 시 안내 문구가 **실제로 존재하지 않는 UI 를 지시**하는 문제
  (커넥터 설정의 Request Headers 항목을 안내하나 해당 입력란이 없음)
- 원격 모드에서 토큰 사용량 수집이 불가능한 구조적 한계

---

> 회사 도메인·서버 주소·포트 등 인프라 정보는 `[SERVER_URL]` 형태의 플레이스홀더로 대체하였습니다.