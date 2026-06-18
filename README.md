# IP 관리 포탈 (Ipmgmt)

여러 서브넷을 스캔해 **사용중인 IP**를 찾고, 각 IP의 **OS / 호스트명 / Uptime / 열린 포트**를
수집해 웹 포탈에서 보여주는 IP 관리 도구입니다.

데이터센터마다 **수집기(collector)** 프로세스를 돌리고, 모든 데이터를 한 곳의
**중앙 포탈(portal)** 로 모아 **20개 이상의 데이터센터를 통합 조회**할 수 있습니다.

```
 ┌──────────────┐   scan(nmap)    ┌──────────────┐
 │ DC: Seoul    │── 10.10.0.0/24 ─│ collector    │──┐
 └──────────────┘                 └──────────────┘  │  HTTPS push
 ┌──────────────┐                 ┌──────────────┐  │  (X-API-Key)
 │ DC: Tokyo    │──────────────── │ collector    │──┤
 └──────────────┘                 └──────────────┘  ▼
 ┌──────────────┐                 ┌──────────────┐ ┌─────────────────────┐
 │ DC: Virginia │──────────────── │ collector    │▶│  중앙 포탈 (Flask)   │
 └──────────────┘                 └──────────────┘ │  + SQLite + 웹 UI    │
        ...  (20+ 데이터센터)                       └─────────────────────┘
```

## 왜 push 방식인가
데이터센터는 보통 방화벽/NAT 뒤에 있어 외부에서 들어오는 접속이 막혀 있습니다.
각 collector가 **아웃바운드 HTTPS로 중앙 포탈에 결과를 올리는(push)** 구조라면,
중앙에서 각 DC로 접속할 필요가 없어 20개 이상으로 확장하기 쉽습니다.

---

## 구성요소

| 경로 | 역할 |
|------|------|
| `collector/scanner.py`   | nmap 래퍼. 서브넷 스캔 → OS/호스트명/uptime/포트 추출 (+데모 모드) |
| `collector/collector.py` | DC 안에서 주기적으로 스캔 후 포탈로 push |
| `portal/app.py`          | Flask: ingest API + 조회 API + 웹 UI |
| `portal/database.py`     | SQLite 저장소 (datacenters, hosts) |
| `portal/templates/`,`static/` | 대시보드 / DC 상세 웹 페이지 |
| `common/hostrecord.py`   | collector·portal 공용 호스트 레코드 스키마 |

---

## 빠른 시작 (데모 — nmap/네트워크 불필요)

```bash
pip install -r requirements.txt
./scripts/demo.sh          # 포탈 실행 + 4개 DC의 합성 데이터 push
# 브라우저에서 http://127.0.0.1:8000
```

`scripts/demo.sh` 는 합성(synthetic) 호스트를 생성하므로 실제 nmap·root·네트워크 없이도
전체 흐름과 웹 UI를 확인할 수 있습니다.

---

## 실제 운영

### 1) 중앙 포탈 실행 (한 곳)

```bash
pip install Flask
export INGEST_API_KEY="강력한-공유키"        # collector 인증용
export PORTAL_DB="/var/lib/ipmgmt/portal.db"
export PORT=8000
python -m portal.app
```

> 운영에서는 reverse proxy(nginx) + TLS 뒤에 두고, `gunicorn -w 4 'portal.app:app'`
> 같은 WSGI 서버로 띄우는 것을 권장합니다.

### 2) 데이터센터마다 collector 실행

각 DC 호스트에 nmap을 설치하고 (`apt-get install -y nmap`), 설정 파일을 만듭니다.

```bash
cp collector/config.example.json collector/config.json
# datacenter.id, portal_url, api_key, subnets, scan_interval_seconds 편집
sudo python -m collector.collector --config collector/config.json
```

* **OS 탐지와 uptime 추정은 root 권한(raw socket)이 필요**합니다.
  root가 아니면 자동으로 TCP connect 스캔으로 폴백하여 IP/호스트명/포트만 수집합니다.
* `--once` 로 1회만 스캔, 옵션 없이 실행하면 `scan_interval_seconds` 주기로 반복합니다.

설정 파일 없이 인라인 옵션으로도 실행 가능합니다:

```bash
sudo python -m collector.collector \
  --portal https://portal.example.com --api-key "$KEY" \
  --dc dc-seoul-01 --dc-name "Seoul DC 1" \
  --subnets 10.10.0.0/24,10.10.1.0/24 --interval 900
```

### systemd 유닛 예시 (collector)

```ini
# /etc/systemd/system/ipmgmt-collector.service
[Unit]
Description=IP management subnet collector
After=network-online.target

[Service]
WorkingDirectory=/opt/Ipmgmt
ExecStart=/usr/bin/python3 -m collector.collector --config /opt/Ipmgmt/collector/config.json
Restart=always
RestartSec=30
# OS 탐지/uptime을 위해 root 또는 다음 capability 필요
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
```

---

## 수집 정보

각 사용중 IP에 대해 다음을 수집/표시합니다.

* **IP 주소** / **서브넷**
* **호스트명** (reverse DNS, nmap `-R`)
* **OS** 추정명 + **정확도(%)** (nmap `-O --osscan-guess`)
* **Uptime** 및 **마지막 부팅 시각** (nmap TCP timestamp 기반 추정)
* **MAC / 벤더**, **열린 포트**

> Uptime은 대상 호스트에 열린 포트가 있고 TCP timestamp 옵션이 켜져 있을 때 nmap이
> 추정합니다. 모든 호스트에서 나오지는 않으며, 더 정확한 값이 필요하면 향후 SNMP
> `sysUpTime` 수집기를 추가할 수 있습니다(`scanner.py`에 확장 지점 준비됨).

---

## HTTP API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/v1/ingest` | collector → 포탈 push (`X-API-Key` 필요) |
| GET  | `/api/v1/stats` | 전체 DC 수 / IP 수 / OS 분포 |
| GET  | `/api/v1/datacenters` | DC 목록 + 요약 |
| GET  | `/api/v1/hosts?dc=&os_family=&subnet=&q=&limit=&offset=` | 호스트 조회/필터 |
| GET  | `/` , `/dc/<id>` | 웹 대시보드 / DC 상세 |
| GET  | `/healthz` | 헬스체크 |

### ingest 페이로드 예시

```json
{
  "datacenter": {"id": "dc-seoul-01", "name": "Seoul DC 1", "location": "Seoul, KR"},
  "scan": {"started_at": "...", "finished_at": "...", "subnets": ["10.10.0.0/24"]},
  "hosts": [
    {"ip": "10.10.0.20", "hostname": "web01", "os_name": "Ubuntu 22.04",
     "os_family": "Linux", "os_accuracy": 96, "uptime_seconds": 432000,
     "last_boot": "2026-06-13 00:00:00", "open_ports": [22, 80, 443],
     "subnet": "10.10.0.0/24"}
  ]
}
```

---

## 보안 메모

* `INGEST_API_KEY` 를 반드시 강력한 값으로 설정하고, 포탈은 TLS 뒤에 둡니다.
* 스캔은 본인이 관리 권한을 가진 네트워크에서만 수행하세요.
* `config.json` 에는 API 키가 들어가므로 `.gitignore` 에 의해 커밋되지 않습니다.

## 향후 확장
* DC별 개별 API 키 / 인증 토큰
* 호스트 변경 이력 / 사라진 IP 추적, 알림
* SNMP·WMI 기반 정밀 uptime/인벤토리
* PostgreSQL 백엔드로 교체 (database.py 인터페이스 유지)
