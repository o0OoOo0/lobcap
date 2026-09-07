# Binance 수집기 배포 가이드 — 무료 서버 편

## 0. 결론 먼저

| 옵션 | 판정 | 이유 |
|---|---|---|
| **Oracle Cloud Always Free** | **✓ 추천** | 서울/도쿄 리전 선택 가능, 200GB 스토리지, 영구 무료 |
| Google Cloud 무료 티어 | **✗ 사용 불가** | **미국 리전만 제공 → Binance가 451로 차단** |
| AWS 무료 티어 | △ | 2025년부터 크레딧 방식으로 변경, 영구 무료 아님 |
| Fly.io / Railway / Render 무료 | ✗ | 스토리지 휘발성, 유휴 시 슬립 → 24시간 수집 부적합 |
| 집 PC / 라즈베리파이 | △ 백업안 | 정전·인터넷 끊김으로 데이터 구멍 |

---

## 1. ⚠️ 가장 중요한 함정 — 서버 리전

**Binance는 미국 IP를 공개 시세 API까지 차단합니다.**

```
HTTP 451 Unavailable For Legal Reasons
"Service unavailable from a restricted location according to 'b. Eligibility'"
```

Restricted Location에는 **미국, 말레이시아, 캐나다 온타리오주** 등이 포함되고,
2022년 11월부터는 인증 없는 read-only 공개 엔드포인트까지 막혔습니다.

이게 왜 치명적이냐면:

- **Google Cloud 무료 티어(e2-micro)는 us-west1 / us-central1 / us-east1 만 제공합니다.**
  전부 미국 → Binance 접속 불가. 무료 티어 중 가장 유명한 선택지가 여기서 탈락합니다.
- PythonAnywhere, Bubble 등 미국 호스팅도 같은 이유로 안 됩니다.

**권장 리전 (Binance 서버는 AWS ap-northeast-1 도쿄)**

| 리전 | 지연 | 비고 |
|---|---|---|
| 도쿄 (ap-tokyo-1) | 가장 낮음 | Binance와 같은 도시 |
| 서울 (ap-seoul-1) | 낮음 | 관리 편의성 |
| 싱가포르 (ap-singapore-1) | 중간 | 도쿄 용량 부족 시 대안 |

Phase 3에서 레이턴시를 실측할 때 이 선택이 그대로 숫자로 나오므로,
가능하면 **도쿄**를 고르세요.

---

## 2. Oracle Cloud Always Free 세팅

### 2.1 현재 제공량 (2026년 9월 기준)

2026년 6월 15일부로 ARM 할당량이 **절반으로 줄었습니다.**

| 자원 | 이전 | 현재 |
|---|---|---|
| Ampere A1 (ARM) | 4 OCPU / 24GB | **2 OCPU / 12GB** |
| AMD 마이크로 | 2 × (1/8 OCPU / 1GB) | 변동 없음 |
| 블록 스토리지 | 200GB | **변동 없음** |
| 아웃바운드 전송 | 10TB/월 | 변동 없음 |

수집기에는 **2 OCPU / 12GB로 차고 넘칩니다.** 스토리지 200GB가 그대로인 게 핵심이에요.

> 기존에 4 OCPU 인스턴스를 쓰던 사용자는 2026년 8월 18일 이후 종료 대상이었습니다.
> 신규 생성은 처음부터 2 OCPU로 잡으면 됩니다.

### 2.2 가입 시 절대 실수하면 안 되는 것

**홈 리전(Home Region)은 가입할 때 정해지고 나중에 바꿀 수 없습니다.**

가입 화면에서 반드시 **Japan East (Tokyo)** 또는 **South Korea Central (Seoul)** 선택.
실수로 US 리전을 골랐다면 계정을 새로 만드는 수밖에 없습니다.

신용카드가 필요하지만 (본인 확인용) 유료 전환하지 않는 한 청구되지 않습니다.

### 2.3 인스턴스 생성

```
Compute → Instances → Create Instance

Image:  Canonical Ubuntu 24.04 (ARM 이미지 — aarch64)
Shape:  VM.Standard.A1.Flex
        OCPU 2, Memory 12 GB
Boot volume: 50 GB
Networking: 공인 IPv4 할당 (기본값)
SSH key: 로컬에서 생성한 공개키 붙여넣기
```

**"Out of host capacity" 에러가 나면** — 가장 흔한 Oracle 이슈입니다.

1. 다른 Availability Domain(AD-1/2/3)으로 바꿔서 재시도
2. OCPU를 1로 낮춰서 시도 (수집기엔 1 OCPU도 충분)
3. 그래도 안 되면 **AMD 마이크로(VM.Standard.E2.1.Micro)로 대체.**
   1 OCPU / 1GB로 빈약하지만 **용량 부족이 거의 없고**, Python 수집기가
   gzip JSONL을 쓰는 정도는 충분히 감당합니다. 2대까지 무료라
   심볼을 나눠 돌릴 수도 있습니다.
4. 시간대를 바꿔가며 재시도 (Oracle이 수시로 용량을 추가함)

### 2.4 데이터용 블록 볼륨 추가

부트 볼륨 50GB를 쓰면 **150GB가 남습니다.** 이걸 데이터 전용으로 붙이세요.

```
Storage → Block Volumes → Create Block Volume
  Size: 150 GB
  Volume Performance: Lower Cost   ← 반드시 이걸로. Balanced는 유료
→ 인스턴스에 Attach (Attachment type: Paravirtualized)
```

서버에서 마운트:

```bash
# 디스크 확인
lsblk

# 포맷 & 마운트 (예: /dev/sdb)
sudo mkfs.ext4 /dev/sdb
sudo mkdir -p /data
sudo mount /dev/sdb /data
sudo chown -R ubuntu:ubuntu /data

# 재부팅 후 자동 마운트 — UUID로 걸어야 안전
echo "UUID=$(sudo blkid -s UUID -o value /dev/sdb) /data ext4 defaults,_netdev,nofail 0 2" \
  | sudo tee -a /etc/fstab
sudo mount -a && df -h /data
```

> `nofail` 을 꼭 넣으세요. 볼륨이 안 붙었을 때 부팅이 멈추는 걸 막아줍니다.

---

## 3. 서버 기본 설정

### 3.1 접속 & 패키지

```bash
ssh -i ~/.ssh/oracle_key ubuntu@<공인IP>

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git curl unzip
```

### 3.2 Oracle 특유의 방화벽 함정

Oracle Ubuntu 이미지는 **iptables에 기본 차단 규칙이 들어있습니다.**
수집기는 아웃바운드만 쓰므로 문제가 안 되지만, 나중에 뭔가 안 될 때
여기를 의심하세요. 인바운드는 SSH(22)만 열어두면 됩니다.

```bash
sudo iptables -L INPUT -n --line-numbers   # 확인용
```

클라우드 쪽 Security List도 별도로 존재합니다(VCN → Security Lists).
**둘 다** 통과해야 하는 구조라 헷갈리기 쉽습니다.

### 3.3 접속 테스트 — 배포 전에 반드시

```bash
# 451이 뜨는지 먼저 확인. 여기서 막히면 리전이 잘못된 것
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://fapi.binance.com/fapi/v1/ping"
# 200 이어야 정상. 451 이면 리전 문제

# 틱 크기 확인
curl -s "https://fapi.binance.com/fapi/v1/exchangeInfo" \
 | python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d['symbols']:
    if s['symbol'] in ('BTCUSDT','ETHUSDT','SOLUSDT'):
        f={x['filterType']:x for x in s['filters']}
        print(s['symbol'],'tick',f['PRICE_FILTER']['tickSize'],'lot',f['LOT_SIZE']['stepSize'])
"
```

### 3.4 코드 배치

```bash
mkdir -p ~/lobcap && cd ~/lobcap
# collector.py, rebuild.py, quality.py, healthcheck.sh 업로드
python3 -m venv .venv
.venv/bin/pip install websockets aiohttp
chmod +x healthcheck.sh
```

---

## 4. systemd로 상시 운영

### 4.1 템플릿 유닛 — 심볼별 프로세스

심볼마다 별도 프로세스로 띄웁니다. 하나가 죽어도 나머지는 살아있게요.

`/etc/systemd/system/lobcap@.service`:

```ini
[Unit]
Description=LOB collector (%i)
After=network-online.target data.mount
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/lobcap
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/lobcap/.venv/bin/python collector.py \
    --symbol %i --market futures --out /data --snap-interval 300
Restart=always
RestartSec=5
StandardOutput=append:/data/logs/%i.log
StandardError=append:/data/logs/%i.log

# 디스크가 꽉 차면 시스템이 마비되므로 메모리도 제한
MemoryMax=2G

[Install]
WantedBy=multi-user.target
```

```bash
mkdir -p /data/logs
sudo systemctl daemon-reload
sudo systemctl enable --now lobcap@btcusdt
sudo systemctl enable --now lobcap@ethusdt
sudo systemctl enable --now lobcap@solusdt

systemctl status 'lobcap@*'
tail -f /data/logs/btcusdt.log
```

### 4.2 로그 로테이션

로그가 디스크를 채워서 수집이 멈추는 게 흔한 사고입니다.

`/etc/logrotate.d/lobcap`:

```
/data/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## 5. 죽었는지 알아채기 — 이게 제일 중요합니다

**수집기가 조용히 죽는 것이 최악의 시나리오입니다.**
3일 뒤에 알아차리면 그 3일은 영영 못 구합니다.

### 5.1 Healthchecks.io (무료) — dead man's switch

1. https://healthchecks.io 가입 (무료 20개 체크)
2. 체크 생성 → Period 15분, Grace 10분
3. Ping URL 복사
4. **핵심: ping이 안 오면 알림이 옵니다.** 서버가 통째로 죽어도 감지됩니다.
   (서버에서 알림을 보내는 방식은 서버가 죽으면 같이 죽어서 무용지물입니다)

```bash
# ~/.bashrc 또는 크론에 환경변수로
export HC_PING_URL="https://hc-ping.com/<발급받은-uuid>"

crontab -e
```

```cron
*/10 * * * * DATA_ROOT=/data HC_PING_URL=https://hc-ping.com/xxxx /home/ubuntu/lobcap/healthcheck.sh >> /data/logs/health.log 2>&1
```

`healthcheck.sh`가 확인하는 것:
- 심볼별 최신 depth 파일이 5분 이내 갱신됐는가
- 디스크 여유가 10GB 이상인가
- systemd 유닛이 active인가

### 5.2 매일 품질 점검 자동화

```cron
5 1 * * * cd /home/ubuntu/lobcap && for s in btcusdt ethusdt solusdt; do \
  .venv/bin/python quality.py --data /data --market futures --symbol $s \
  --date $(date -u -d yesterday +\%Y-\%m-\%d); done >> /data/logs/quality.log 2>&1
```

---

## 6. 백업 — 무료 티어는 언제든 사라질 수 있습니다

Oracle은 **예고 없이 정책을 바꿉니다.** 2026년 6월 ARM 할당량 반토막이 그 증거고,
공지 없이 문서만 수정했습니다. 유휴 인스턴스 회수 정책도 있습니다.

**데이터를 서버에만 두지 마세요.**

### 6.1 rclone으로 주기적 동기화

```bash
curl https://rclone.org/install.sh | sudo bash
rclone config     # Google Drive(15GB 무료) 또는 Backblaze B2(10GB 무료)
```

```cron
# 매일 새벽, 어제까지의 데이터를 클라우드로 동기화
30 2 * * * rclone sync /data/market=futures remote:lobcap/ \
  --exclude "logs/**" --transfers 4 --checkers 8 >> /data/logs/backup.log 2>&1
```

3심볼 3개월이면 압축 후 **약 30GB**입니다. 무료 클라우드 용량을 넘길 수 있으니
주기적으로 로컬 PC나 외장 하드로도 내려받으세요.

### 6.2 인스턴스 회수 방지

Oracle은 Always Free 인스턴스가 **장기간 유휴 상태면 회수**할 수 있습니다.
수집기는 CPU를 거의 안 쓰므로 위험군입니다. 네트워크 트래픽이 계속 있어
실제로 회수되는 경우는 드물지만, 걱정되면 가벼운 CPU 작업을 주기적으로 걸어두세요
(예: 매일 `rebuild.py`를 서버에서 돌리기 — 어차피 필요한 작업입니다).

---

## 7. 용량 계획

| 심볼 | msg/s | 원본 JSON | gzip 후 |
|---|---|---|---|
| BTCUSDT perp @0ms | 50~200 | ~2 GB/일 | ~200 MB/일 |
| ETHUSDT perp | 30~100 | ~1 GB/일 | ~100 MB/일 |
| SOLUSDT perp | 5~30 | ~300 MB/일 | ~30 MB/일 |

3심볼 합 **약 330MB/일 → 10GB/월 → 3개월 30GB.**
데이터 볼륨 150GB면 1년 이상 버팁니다.

**첫날 실측 후 확정하세요:**

```bash
du -sh /data/market=futures/symbol=*/kind=depth/date=$(date -u +%Y-%m-%d)
```

---

## 8. 배포 체크리스트

- [ ] Oracle 가입 시 **홈 리전을 도쿄/서울로** 선택 (되돌릴 수 없음)
- [ ] `curl fapi.binance.com/fapi/v1/ping` → **200** 확인 (451이면 리전 실패)
- [ ] 인스턴스 생성 (A1.Flex 2 OCPU / 12GB, 안 되면 AMD 마이크로)
- [ ] 150GB 블록 볼륨 생성 시 **Lower Cost** 선택 (Balanced는 유료)
- [ ] `/etc/fstab`에 `nofail` 포함해서 자동 마운트
- [ ] `exchangeInfo`로 틱/lot 크기 확인 및 기록
- [ ] systemd 템플릿 유닛으로 심볼별 기동, `enable` 까지
- [ ] logrotate 설정
- [ ] Healthchecks.io 연동 + 크론 등록, **일부러 수집기를 죽여서 알림 오는지 테스트**
- [ ] rclone 백업 크론 등록, 첫 동기화 성공 확인
- [ ] 24시간 후 `quality.py` 전 항목 PASS 확인
- [ ] 하루치 실제 용량 측정 → 3개월 계획 확정
- [ ] **재부팅 테스트** — `sudo reboot` 후 수집이 자동 재개되는지

---

## 9. 흔한 사고와 대처

| 증상 | 원인 | 대처 |
|---|---|---|
| 451 에러 | 서버가 미국 리전 | 리전 변경 (계정 재생성 필요할 수 있음) |
| "Out of host capacity" | ARM 용량 부족 | AD 변경 / OCPU 낮추기 / AMD 마이크로 |
| 재부팅 후 수집 중단 | fstab 누락, enable 안 함 | `systemctl enable`, fstab 확인 |
| 시퀀스 갭 폭증 | 네트워크 불안정 | 재접속 로그 확인, 리전 변경 검토 |
| 디스크 풀 | 로그 로테이션 미설정 | logrotate, 오래된 데이터 아카이빙 |
| 데이터 파일이 안 커짐 | WS는 붙었는데 구독 실패 | 로그에서 subscribe 응답 확인 |

---

## 10. 오늘 당장

**데이터는 지나가면 못 구합니다.** FPGA도 시뮬레이터도 나중에 만들 수 있지만,
오늘 흘러간 호가는 오늘 안 받으면 끝입니다.

1. Oracle 가입 → **도쿄 리전** (10분)
2. 인스턴스 생성, 볼륨 마운트 (20분)
3. `curl ping` 200 확인 (1분)
4. 코드 올리고 `lobcap@btcusdt` 하나만 먼저 기동 (10분)
5. 로그 보면서 파일이 커지는지 확인
6. 나머지 심볼, 헬스체크, 백업은 내일 붙여도 됩니다

완벽하게 세팅한 뒤 시작하려다 일주일을 날리는 것보다,
**오늘 BTCUSDT 하나라도 받기 시작하는 게 낫습니다.**
