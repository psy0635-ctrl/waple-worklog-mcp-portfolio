# 배포 운영 가이드 (systemd)

## 1. 배경

초기 배포에서는 `nohup`으로 서버를 백그라운드 실행하였습니다.

```bash
nohup python server.py --transport streamable-http --port 8010 > ~/mcp.log 2>&1 &
```

이 방식은 **서버가 재부팅되면 프로세스가 함께 사라진다는 문제**가 있습니다.
2026-07-26 실제로 서버 재부팅이 발생하여 MCP 서버가 중단되었고,
외부 접속 시 502 응답이 반환되었습니다.
로그에는 예외가 남아 있지 않아, 코드 오류가 아닌 프로세스 소멸임을 확인하였습니다.

## 2. 제약 조건

배포 서버 계정의 `sudo` 권한은 nginx 관련 명령으로만 제한되어 있습니다.

```bash
sudo -l
# (root) NOPASSWD: /usr/bin/systemctl start nginx, ... /usr/bin/nano /etc/nginx/*
```

따라서 `/etc/systemd/system`에 서비스를 등록하는 일반적인 방법은 사용할 수 없습니다.

## 3. 해결 방법 — 사용자 systemd + linger

루트 권한 없이 사용 가능한 **사용자 단위 systemd**를 사용하였습니다.
다만 기본 설정에서는 사용자가 로그아웃하면 서비스도 종료되므로,
`linger` 옵션을 활성화하여 로그인 여부와 무관하게 상시 동작하도록 하였습니다.

### 설치 절차

```bash
# 1) linger 활성화 (로그인하지 않아도 사용자 서비스가 유지된다)
loginctl enable-linger
loginctl show-user $USER | grep -i linger   # Linger=yes 확인

# 2) 서비스 파일 배치
mkdir -p ~/.config/systemd/user
cp deploy/waple-mcp.service ~/.config/systemd/user/

# 3) 기존 nohup 프로세스 종료 (포트 충돌 방지)
pkill -f "server.py --transport streamable-http"

# 4) 서비스 등록 및 시작
systemctl --user daemon-reload
systemctl --user enable --now waple-mcp
systemctl --user status waple-mcp --no-pager
```

## 4. 운영 명령

| 목적 | 명령 |
| --- | --- |
| 상태 확인 | `systemctl --user status waple-mcp` |
| 재시작 | `systemctl --user restart waple-mcp` |
| 중지 | `systemctl --user stop waple-mcp` |
| 로그 확인 | `tail -f ~/mcp.log` |
| 외부 생존 확인 | `curl -s -o /dev/null -w "%{http_code}\n" [SERVER_URL]/llm/mcp` |

생존 확인 시 **406이 정상 응답**입니다.
MCP 프로토콜이 `Accept: text/event-stream` 헤더를 요구하기 때문에,
헤더 없이 요청하면 406을 반환합니다.
`000` 또는 `502`가 반환되면 서버가 중단된 상태입니다.

## 5. 검증 결과 (2026-07-26)

| 항목 | 방법 | 결과 | 등급 |
| --- | --- | --- | --- |
| 서비스 기동 | `systemctl --user status` | active (running) | 실물 검증 |
| 포트 점유 | `ss -tlnp \| grep 8010` | 127.0.0.1:8010 | 실물 검증 |
| 외부 접속 | `curl` 상태코드 | 406 | 실물 검증 |
| 비정상 종료 후 자동 복구 | `kill -9` 후 PID 비교 | PID 변경, 서비스 유지 | 실물 검증 |
| 재부팅 후 자동 기동 | `is-enabled`, `Linger` 확인 | enabled / Linger=yes | 설정 확인(재부팅 미실시) |

재부팅 검증을 수행하지 않은 이유는, 동일 서버에서 다른 팀의 API가 함께 운영되고 있어
임의 재부팅이 타 서비스 중단을 유발하기 때문입니다.
