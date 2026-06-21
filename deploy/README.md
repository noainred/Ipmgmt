# 배포 (Production)

추천 스택: **PostgreSQL + gunicorn(Flask) + nginx** (포탈), **systemd** (각 DC collector).

```
                ┌─────────────── 데이터센터 호스트들 ───────────────┐
                │  systemd: ipmgmt-collector  (nmap 스캔 → push)     │
                └───────────────────────┬───────────────────────────┘
                                        │ HTTPS (X-API-Key)
                         ┌──────────────▼──────────────┐
   브라우저 ── 80/443 ──▶│ nginx ─▶ gunicorn(portal x4) │─▶ PostgreSQL
                         └─────────────────────────────┘
```

## 1. 포탈 (Docker Compose)

```bash
cd deploy
cp .env.example .env          # INGEST_API_KEY / ADMIN_TOKEN / POSTGRES_PASSWORD 설정
docker compose up -d --build
# http://localhost/  ,  http://localhost/settings
```

구성요소:
- **db** — `postgres:16`, 데이터는 `pgdata` 볼륨에 영속화
- **portal** — `deploy/Dockerfile` 빌드, `gunicorn -c deploy/gunicorn.conf.py portal.app:app`
  (워커 4, `DATABASE_URL` 로 db 연결, 워커별 자체 커넥션)
- **nginx** — 80에서 포탈로 프록시, `client_max_body_size 25m`(대용량 ingest 대비).
  TLS는 `deploy/certs/` 에 인증서를 두고 `nginx.conf`/compose의 443 블록을 활성화

### Docker 없이 (단일 호스트)

```bash
pip install -r deploy/requirements-portal.txt
export DATABASE_URL="postgresql://ipm:비밀번호@127.0.0.1:5432/ipmgmt"
export INGEST_API_KEY=... ADMIN_TOKEN=...
gunicorn -c deploy/gunicorn.conf.py portal.app:app   # 0.0.0.0:8000
```

> SQLite로도 그대로 뜹니다(`DATABASE_URL` 미설정 시 `PORTAL_DB` 파일 사용). 단 다중
> 워커·HA가 필요하면 PostgreSQL을 쓰세요.

## 2. Collector (각 DC, systemd)

각 데이터센터 호스트에서 한 개씩 실행합니다.

```bash
# 1) 의존성
apt-get install -y python3 python3-pip nmap
pip3 install requests
# 2) 배포 + 설정
git clone <repo> /opt/Ipmgmt
cp /opt/Ipmgmt/collector/config.example.json /opt/Ipmgmt/collector/config.json
#   -> datacenter.id, portal_url, api_key 설정 (subnets는 웹 설정에서 받아도 됨)
# 3) 서비스 등록
cp /opt/Ipmgmt/deploy/ipmgmt-collector.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ipmgmt-collector
journalctl -u ipmgmt-collector -f
```

* OS 탐지/uptime은 raw socket이 필요 → 유닛에 `CAP_NET_RAW CAP_NET_ADMIN` 부여.
* 서브넷/스캔주기/사용여부는 포탈 `/settings` 에서 관리(collector가 pull). collector 재배포 불필요.

## 환경변수 요약

| 변수 | 대상 | 설명 |
|------|------|------|
| `DATABASE_URL` | 포탈 | `postgresql://user:pass@host:5432/db` (없으면 SQLite) |
| `PORTAL_DB` | 포탈 | SQLite 파일 경로 (기본 `portal.db`) |
| `INGEST_API_KEY` | 포탈·collector | ingest/config pull 인증 공유키 |
| `ADMIN_TOKEN` | 포탈 | 웹 설정 페이지 쓰기 권한 |
| `GUNICORN_WORKERS` | 포탈 | gunicorn 워커 수 (기본 4) |
| `PORT` | 포탈 | 리슨 포트 (기본 8000) |

## 운영 메모
- **백업**: PostgreSQL `pg_dump` (또는 관리형 DB의 스냅샷). SQLite면 파일/Litestream.
- **확장**: 읽기 부하가 커지면 대시보드 집계를 Redis로 캐싱. 100+ DC/이력이면 TimescaleDB 검토.
- **보안**: 반드시 TLS 뒤에 두고 토큰을 강력하게. 스캔은 관리 권한 있는 네트워크에서만.
