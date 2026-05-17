# WDBX Radio Toolbox — Operations Runbook
*Last updated: 2026-05-17*

This document is the persistent operational reference. It covers the live production environment, known quirks, deployment procedures, alerting setup, and deferred work. Keep it current whenever something changes.

---

## 1. Production Environment

| Component | Detail |
|-----------|--------|
| Service host | Ubuntu 22.04 LTS, hostname `wdbx-stream2-3020` |
| Kernel | 6.8.0-110-generic (HWE) |
| App path | `/home/wdbx/wdbx-toolbox/` |
| Service user | `wdbx` |
| Web UI | `http://<station-LAN-IP>:8000` |
| Database | `/home/wdbx/wdbx-toolbox/wdbx.db` (SQLite) |
| Log file | `/home/wdbx/wdbx-toolbox/logs/wdbx.log` (rotating, 10 MB × 5) |
| Systemd unit | `wdbx-toolbox.service` |
| NAS | Iomega StorCenter px6-300d at `192.168.2.185` |
| NAS share | `//192.168.2.185/Add to Share` mounted at `/mnt/wdbx-share` |
| NAS creds | `/etc/samba/wdbx-nas.creds` (root-owned, chmod 600) |
| Local staging | `/home/wdbx/Desktop/Download-Folder` (NAS failsafe) |
| Archive root | `/mnt/wdbx-share/Shows/AutoArchive/` |

---

## 2. Current Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 0 — NAS mount, repo setup, SMTP | **Complete** | See quirks below |
| 0.5 — Onboarding wizard | **Complete** | Shows imported, durations set |
| 1 — Archive Manager MVP | **Complete** | Running in production |
| 2 — Archive Manager Complete | In progress | Schedule change detection partial |
| 3–8 | Not started | See dev_plan_v2.md |

---

## 3. Infrastructure Quirks — Read This First

### 3.1 NAS SMB Signing (Critical)
The Iomega px6-300d required SMB packet signing. Linux kernel 6.8 CIFS client (v2.47) fails signature verification against this firmware even with correct credentials — error `-13` in dmesg. **Userspace `smbclient` works fine; kernel mount fails.**

**Fix applied 2026-05-17:** Disabled "SMB Server Signing" in the NAS web UI at `http://192.168.2.185` → Protocols → Windows file sharing. The fstab entry explicitly requests `vers=3.1.1`.

**If the NAS stops mounting after a firmware update:** Check `dmesg | grep -i cifs` for `sign fail`. If it returns, SMB signing got re-enabled on the NAS (some firmware updates reset this). Log into the NAS admin UI and disable it again.

**fstab entry:**
```
//192.168.2.185/Add\040to\040Share  /mnt/wdbx-share  cifs  credentials=/etc/samba/wdbx-nas.creds,uid=wdbx,gid=wdbx,iocharset=utf8,vers=3.1.1,_netdev,nofail  0  0
```

### 3.2 NAS Credentials
- Auth user: `rsync`
- Credentials file: `/etc/samba/wdbx-nas.creds`
- If mount fails with error 13 AND no `sign fail` in dmesg → wrong password. Log into NAS admin UI, reset the `rsync` account password, update the creds file.

### 3.3 NAS Silent-Drop Detection
`nas_is_writable()` in `archive_manager/nas.py` checks `/proc/mounts` to confirm the path is an active CIFS mount before probing. An unmounted mountpoint (empty local directory) is never mistaken for a healthy NAS. If the mount drops, the UI shows a red "NAS unreachable" banner and downloads route to local staging.

### 3.4 Root Disk Usage
As of 2026-05-17: `/dev/sda3` is 94% full (405G used, 30G free). The bulk of this is old backups from the pre-toolbox `dl-toggle.py` script in `/home/wdbx/Desktop/Download-Folder`. These have been verified as synced to NAS and are pending deletion — see Section 7.

---

## 4. Deployment Procedure

All code lives at `https://github.com/metaphorever/wdbx-radio-toolbox`. Deploy by pulling on the Ubuntu box.

```bash
# Pull latest
sudo -u wdbx git -C /home/wdbx/wdbx-toolbox pull

# Restart the service (always required after Python or template changes)
sudo systemctl restart wdbx-toolbox

# Verify
systemctl status wdbx-toolbox --no-pager | head -10
tail -10 /home/wdbx/wdbx-toolbox/logs/wdbx.log
```

**Note:** Run git as the `wdbx` user, not root. Running as root causes a "dubious ownership" error.

---

## 5. Configuration

### 5.1 config.yaml (committed to git)
Master config with defaults. All paths, schedule intervals, processing constants. Do not put credentials here.

### 5.2 config.local.yaml (gitignored, lives on server only)
Overrides and credentials. As of 2026-05-17:

```yaml
archive:
  filename_template: '{date} - {show} - WDBX'

database:
  path: /home/wdbx/wdbx-toolbox/wdbx.db

local_staging:
  path: /home/wdbx/Desktop/Download-Folder

logging:
  file: /home/wdbx/wdbx-toolbox/logs/wdbx.log

nas:
  mount_point: /mnt/wdbx-share
  archive_path: /mnt/wdbx-share/Shows/AutoArchive
  overnight_output_path: /mnt/wdbx-share/overnight-programming

monitoring:
  heartbeat_url: "https://hc-ping.com/b775388e-e398-4fa9-8dc0-37d1fe33c422"

smtp:
  host: "smtp.dreamhost.com"
  port: 587
  user: "monitoring@wdbx.org"
  password: "<in creds file on server>"
  from_addr: "monitoring@wdbx.org"
  to_addr: "opswdbx911fm@gmail.com,metaphorever@gmail.com"
```

### 5.3 Filename Template Note
The old template (`{date} [{show}] - WDBX`) accidentally baked in literal brackets. The canonical template is `{date} - {show} - WDBX`. Old files with brackets in their names are in the archive — do not rename them ad-hoc; this will be addressed in the NAS consolidation project (see Section 7).

---

## 6. Alerting

Two-layer system:

| Layer | Mechanism | Catches |
|-------|-----------|---------|
| Heartbeat | healthchecks.io — app pings every 15 min | App crash, hung process, broken SMTP, anything that kills the scheduler |
| Per-event SMTP | `archive_manager/mailer.py` → DreamHost SMTP | NAS unreachable, scrape failures, individual download failures |

**healthchecks.io dashboard:** Log in to healthchecks.io to see the "WDBX Toolbox" check. If it's red, the app has been silent for >10 minutes. SSH to the Ubuntu box and check `systemctl status wdbx-toolbox` and `journalctl -u wdbx-toolbox -n 50`.

**Testing SMTP:**
```bash
sudo -u wdbx python3 -c "
import sys; sys.path.insert(0,'/home/wdbx/wdbx-toolbox')
from archive_manager.mailer import send_alert
send_alert('Test', 'Test message')
"
```

---

## 7. Deferred Work

### 7.1 Root Disk Cleanup (Low urgency — 30G free)
Old `dl-toggle.py` downloads in `/home/wdbx/Desktop/Download-Folder` (Friday/Monday/Saturday subdirs). Verify each show's files are on the NAS, then delete in waves. Do not delete until verified in person.

### 7.2 NAS Consolidation Project (Separate project)
Full audit and merge of duplicate content across the NAS:
1. Find all episodes across all NAS locations (legacy `Shows/{N - Weekday}/` and new `Shows/AutoArchive/`)
2. Identify canonical version of each episode
3. Consolidate to canonical archive location with canonical names
4. Verify canonical archive is complete
5. Delete duplicates in waves with safety checks
6. Schedule backups of canonical archive to secondary storage

### 7.3 Pacifica Archive Retention Window
Most shows: 30 days. The Groove (groove): 14 days — shorter than typical, monitor. Some talk/public-domain shows: 46–59 days or permanent. Check `expires_at` in the DB if uncertain.

---

## 8. Common Operations

### Check what's downloading / queued
```bash
sqlite3 /home/wdbx/wdbx-toolbox/wdbx.db "SELECT status, COUNT(*) FROM episode GROUP BY status;"
```

### Manually kick the download job (don't wait for the hourly tick)
```bash
sudo -u wdbx python3 -c "
import sys; sys.path.insert(0,'/home/wdbx/wdbx-toolbox')
import logging; logging.basicConfig(level=logging.INFO, format='%(message)s')
from archive_manager.scheduler import _download_job
_download_job()
"
```

### Reset stuck 'downloading' episodes
Episodes can get stuck as `downloading` with no `local_path` if the service restarts mid-download:
```bash
sqlite3 /home/wdbx/wdbx-toolbox/wdbx.db "
UPDATE episode SET status='pending'
WHERE status='downloading' AND (local_path IS NULL OR local_path='');"
```

### Check for archive gap (last saved per show)
```bash
sqlite3 /home/wdbx/wdbx-toolbox/wdbx.db "
SELECT show_key,
  MAX(CASE WHEN status='downloaded' THEN date(air_datetime) END) AS last_saved,
  COUNT(CASE WHEN status='downloaded' THEN 1 END) AS saved,
  COUNT(CASE WHEN status IN ('failed','expired') THEN 1 END) AS lost
FROM episode GROUP BY show_key ORDER BY last_saved ASC;"
```

### View live logs
```bash
journalctl -u wdbx-toolbox -f --no-pager          # live
journalctl -u wdbx-toolbox -n 100 --no-pager       # last 100 lines
tail -f /home/wdbx/wdbx-toolbox/logs/wdbx.log      # file log
```

### Remount NAS manually
```bash
sudo mount -a
mount | grep wdbx-share
```

---

## 9. Incident Log

| Date | Issue | Root Cause | Fix |
|------|-------|-----------|-----|
| 2026-05-17 | Archive page 500 | Stray `{% endif %}` in `archive.html` | Removed duplicate tag |
| 2026-05-17 | NAS unreachable / silent failure | Kernel CIFS 6.8 signature verification fails against px6-300d firmware | Disabled SMB signing on NAS; patched `nas_is_writable()` to check `/proc/mounts` |
| 2026-05-17 | 0-byte fragment not detected | `downloader.py` accepted HTTP 200 with empty body | Added size check — reject 0-byte downloads |
| 2026-05-17 | Expired episodes retried indefinitely | Download job didn't filter by `expires_at` | Skip and mark `expired` when `expires_at < now` |
| 2026-05-17 | No alerting during outage | SMTP not configured; heartbeat not implemented | Configured DreamHost SMTP + healthchecks.io heartbeat |
| 2026-05-17 | Log file never written | `_setup_logging()` not called at startup | Wired up in `web/main.py` |
