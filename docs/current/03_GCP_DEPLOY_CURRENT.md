# Current Deployment — GCP Tokyo

Updated: 2026-09-06

This supersedes `docs/legacy/04_DEPLOY_ORACLE.md`.

## Existing environment

- Cloud: Google Cloud
- project: `project-2878cd89-af59-4d8a-aef`
- VM: `lob-collector-01`
- zone: `asia-northeast1-a`
- machine: `e2-medium`
- OS: Ubuntu 24.04 LTS
- user: `0woo`
- project root: `/home/0woo/lobcap`
- data root: `/data`
- boot disk: ~30 GB
- data disk: ~100 GB Standard Persistent Disk

The VM already exists. Do not recreate it.

## Observed operational state

Prompt 3 preflight on 2026-09-06 found that the mounted `/data` root was owned
by the previous VM user `funheo0:funheo0` with mode `0755`, so user `0woo`
could not write collector output. The data root contained no market data at
that point. Ownership of the `/data` mount root only was corrected to
`0woo:0woo`; a create/remove write probe passed afterward. The filesystem
mount and ownership must both be checked before every deployment or reboot
validation. Existing captured data must not be deleted when correcting mount
ownership.

Prompt 4 completed on 2026-09-06. The one-hour BTCUSDT foreground test ran
from `2026-09-06T14:04:24.517310Z` through
`2026-09-06T15:04:30.446520Z` and passed: 35,273 depth messages, 60,450
aggregate trades, 12 snapshots, zero `pu` mismatches, zero sequence-gap/error/
reconnect events, zero empty five-minute bins, and clean gzip files. The test
crossed 15:00 UTC, and both depth and trade files rolled to the next hour.

The source-controlled unit is `systemd/lobcap@.service`; it is installed as
`/etc/systemd/system/lobcap@.service`. Only `lobcap@btcusdt.service` is enabled
and running. A systemd live test ran for more than ten minutes before reboot,
with new depth, trade, snapshot, and connection lifecycle records.

The reboot test changed the boot ID from
`5ee85ccb-ad35-427b-b493-338730283f31` to
`5d8ce5e5-b8d2-427f-a2f5-63b396e3c2ad`. After boot, `/dev/sdb` remounted on
`/data` as ext4 with owner/group `0woo:0woo`, and the enabled BTCUSDT unit
started automatically at `2026-09-06T15:21:02Z`. The shutdown recorded the
old process's `process_stop`; the new process recorded a distinct
`process_start`, depth/trade connection IDs, and connected events. The reboot
interval is an acquisition boundary, not continuous feed coverage. The first
new depth event's `pu` therefore is not compared with the final pre-reboot
`u`; within the new process segment, all 1,499 comparable pairs among the
first 1,500 depth messages matched.

## Billing rule

This Tokyo VM is not the always-free US e2-micro setup and may consume credits/billable usage.

Codex must not:
- create another VM;
- resize machine;
- create/resize/delete disks;
- change static IP/network/firewall;
- enable paid services;
- change billing configuration;

without explicit user approval.

Ordinary package installation and systemd configuration inside the existing VM are allowed.

## Environment preflight

```bash
hostname
whoami
pwd
df -h /data
findmnt /data
timedatectl status
python3 --version
```

Expected:
- hostname `lob-collector-01`
- user `0woo`
- `/data` mounted

## Python environment

Recommended:

```bash
cd /home/0woo/lobcap
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## systemd target design

Only after 10-minute + 1-hour foreground tests pass.

Template path:
`/etc/systemd/system/lobcap@.service`

Conceptual unit:

```ini
[Unit]
Description=LOB collector %i
Wants=network-online.target
After=network-online.target
RequiresMountsFor=/data

[Service]
Type=simple
User=0woo
WorkingDirectory=/home/0woo/lobcap
ExecStart=/home/0woo/lobcap/.venv/bin/python -m lobcap.collectors.binance --symbol %i --out /data
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Do not enable it until runtime validation passes.

## Useful commands

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lobcap@btcusdt
systemctl status lobcap@btcusdt
journalctl -u lobcap@btcusdt -f
sudo systemctl restart lobcap@btcusdt
sudo systemctl stop lobcap@btcusdt
```

## Reboot test

After enabling:
1. `sudo reboot`
2. reconnect by SSH
3. verify `/data`
4. verify service active
5. verify files resume growing

## Regional-access rule

Do not assume Tokyo can access Binance merely because an older Oracle Tokyo plan expected it.

Test REST/WS from this actual VM first.
Any 451/403/regional restriction is a blocking runtime result.
