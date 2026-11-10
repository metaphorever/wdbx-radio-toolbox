# T610 Archive Server — Build & Operations
*Last updated: 2026-11-09*

The donated Dell PowerEdge T610 is the planned new home for the archive and the toolbox, replacing the aging Iomega px6-300d NAS as primary storage. Streaming stays on the redundant Ubuntu stream PCs.

**Status:** OS mirror, ZFS pool `wdbx`, alerting, power policy and Tailscale are done, and every failure mode below has been rehearsed (§6). The box is still off-site at the developer's home, on the home LAN. The live system (`wdbx-stream2-3020` + NAS) keeps running unchanged; toolbox dev/testing will happen on the T610. Archive sizing: [`nas_consolidation.md`](nas_consolidation.md).

---

## 1. Hardware

| Component | Detail |
|-----------|--------|
| Server | Dell PowerEdge T610, BIOS 6.4.0, service tag `58C8VR1` |
| CPU / RAM | 2× Xeon 5600-series (Westmere), ECC DDR3 |
| Storage controller | Dell PERC H310 cross-flashed to LSI 9211-8i **IT mode** (FW 20.00.07.00-IT, BIOS 7.39.02.00), in rear PCIe slot 2 (x8). Bought pre-flashed from StorageTekPro. |
| Removed | Original PERC H700 (hardware RAID, cannot be flashed to IT mode) + its battery — kept in case |
| Drive bays | 8 hot-swap, full-size 3.5". The SSDs sit in 2.5" adapters. |
| NIC | Broadcom BCM5709 dual GbE (`eno1` cabled, `eno2` unused) |
| Management | iDRAC6 (showed `192.168.0.120` at POST on the home LAN) |

### Bay map

Bay numbers are the labels on the front of the server. **They don't match the slot numbers the LSI utility shows**, and Linux device names (`sda`, `sdg`, …) change whenever a drive is reseated. Identify drives by **serial number** or `/dev/disk/by-id/wwn-*`, never by `sdX`. Label each tray with its serial.

| Bay | Drive | Serial / ID | Role |
|-----|-------|-------------|------|
| 0 | Seagate ST3000DM001 3 TB | `Z501WENR` · `wwn-0x5000c500872db5b4` | Pool `wdbx`, mirror-0 |
| 1 | Hitachi HDS723030 3 TB | `MK0331YHH0MTYA` · `wwn-0x5000cca225ce61d9` | Pool `wdbx`, mirror-0 — **expected to fail**, see below |
| 2 | Seagate ST2000DM001 2 TB | `Z4Z7JV11` | Tested and clean; waiting for a partner (the NAS's idle 2 TB spare) |
| 3 | *empty* | | For testing the NAS spare |
| 4 | Seagate ST600MP0036 600 GB SAS | `WAF0D5DR` | Spare / drill drive |
| 5 | Seagate ST600MP0036 600 GB SAS | `WAF1M542` | Spare / drill drive |
| 6 | Samsung MZ-5S70500 46 GB SSD | `S0PHNEAB700070` | OS mirror — its `md0` member is **partition 1** |
| 7 | Samsung MZ-5S70500 46 GB SSD | `S0PHNEAB700067` | OS mirror — its `md0` member is **partition 2** |

Bays 6/7 were confirmed by pulling each SSD in the drills (they had been recorded the other way round).

### Drive notes

- **Hitachi (bay 1)** was donated with a warning: it had stopped its previous PC from booting. ~89,500 power-on hours (~10 years), 651 reallocated sectors (stable through a full write/read pass), its 15 pending sectors cleared by that pass. It is kept **deliberately** as a drive likely to fail, for real-world experience, which is acceptable only while the NAS and its backup still hold everything. **Replace it before the NAS is retired.** In a mirror the replacement must be **≥ 3 TB**.
- **ST3000DM001 (bay 0)** is a model with a notoriously high failure rate. This unit tested clean: its 392 pending/uncorrectable sectors were rewritten without a single reallocation (half-written sectors, likely from a past power loss). Its 43 CRC errors predate the T610 and didn't increase.
- **Failed and removed:** WD WD20EZBX 2 TB `WD-WXW2A82FP6HC` — SMART FAILED, 2,339 reallocated, 881 pending, 149 uncorrectable. It still holds its donor's data; **physically destroy it** (drill the platters) before e-waste.
- **Also on the shelf:** 4 more ST600MP0036 SAS drives (T10 PI already removed, see §8) and 6+ donated 500 GB drives, earmarked for offline copies of the small pre-2024 year archives.

---

## 2. OS Layout

| Item | Detail |
|------|--------|
| OS | Ubuntu Server 24.04.5 LTS |
| Hostname / user | `wdbx-media` / `wdbx` (matches the runbook's `/home/wdbx/wdbx-toolbox` paths) |
| Timezone | `America/Chicago` |
| Root | mdadm RAID1 `md0` (SSD `…070` partition 1 + SSD `…067` partition 2), ext4 on `md0p1` mounted at `/`. UUID `7767cbbe:5afd57a6:185cc2cc:a05b3f7d` |
| Boot | Legacy BIOS. 1 MB `bios_grub` partition on each SSD; GRUB installed on **both** (`grub-pc/install_devices` lists both). Booting from **either SSD alone** is verified (§6). |
| Network | `/etc/netplan/01-wdbx.yaml`, DHCP on `eno1` and `eno2` (`optional: true`). Home LAN address `192.168.0.177` for now — needs a DHCP reservation at the station |

Check the mirror: `cat /proc/mdstat` should show `[UU]`.

---

## 3. ZFS Pool `wdbx`

```
mirror-0: Hitachi 3 TB (bay 1) + Seagate ST3000DM001 3 TB (bay 0)
ashift=12, autoexpand=on, compression=lz4, atime=off, xattr=sa, acltype=posixacl, recordsize=1M
mountpoint=/srv/wdbx, 2.63 TiB available
```

| Dataset | Mountpoint | Toolbox setting |
|---------|------------|-----------------|
| `wdbx/archive` | `/srv/wdbx/archive` | `nas.archive_path` |
| `wdbx/overnight` | `/srv/wdbx/overnight` | `nas.overnight_output_path` |
| `wdbx/staging` | `/srv/wdbx/staging` | `local_staging.path` |

`/srv/wdbx/staging/rescue/` holds a third copy of the 88 files that exist nowhere else on the NAS (13.6 GB). Checksums: `~/rescue.sha256`, with paths relative to that folder, so verify with `cd /srv/wdbx/staging/rescue && sha256sum -c --quiet ~/rescue.sha256`.

**Why mirrors, not RAIDZ:** the drives are donated and mixed-size. RAIDZ counts every drive as the smallest one; mirrors only need each *pair* to match, grow two drives at a time (`autoexpand` grows a pair once both drives are swapped for bigger ones), and rebuild fast. Trade-off: each pair survives one failure, and losing **both drives of one pair loses the pool** — so never pair two drives that are likely to fail together, and keep the archive's other copies until the pool has sound drives.

**Growth path:**
1. Test the NAS's idle 2 TB spare in bay 3, then add the 2 TB pair: `sudo zpool add wdbx mirror /dev/disk/by-id/<ST2000DM001 wwn> /dev/disk/by-id/<spare wwn>` (+2 TB).
2. When the NAS retires, its 2 TB drives become further pairs and replace weak members.

The 6×600 GB `testpool` used to prove out the stack was destroyed on 2026-11-09. Ubuntu's `zfsutils-linux` cron scrubs every pool monthly; ZED emails the result.

---

## 4. Alerting

All local alerts go out by email through the same DreamHost account the toolbox uses. Every path below has fired during a real (drilled) event.

| Piece | Config |
|-------|--------|
| Mail transport | `msmtp` + `msmtp-mta` (provides `/usr/sbin/sendmail`) + `bsd-mailx` (provides `mail`). Config `/etc/msmtprc` (root, chmod 600): `smtp.dreamhost.com:587`, TLS, logfile `/var/log/msmtp.log`. msmtp AppArmor profile left **disabled** (it can block `/etc/aliases` and the logfile) |
| Recipients | `/etc/aliases` maps `root` and `default` to the alert recipients |
| ZFS | `/etc/zfs/zed.d/zed.rc`: `ZED_EMAIL_ADDR="root"`, `ZED_NOTIFY_VERBOSE=1` (scrub and resilver reports arrive, so a monthly email proves the path works) |
| RAID mirror | `mdmonitor` with mdadm's default `MAILADDR root`. Only failures/degradation/test messages are emailed |
| Drive health | `/etc/smartd.conf`: `DEVICESCAN -d removable -n standby -a -m root -s (S/../.././02\|L/../../6/03) -M exec /usr/share/smartmontools/smartd-runner` — short self-test daily 02:00, long Saturdays 03:00. **smartd only finds drives when it starts: run `sudo systemctl restart smartmontools` after adding or removing drives.** |
| Boot-time alerts | `/etc/systemd/system/{mdmonitor,smartmontools,zfs-zed}.service.d/wait-for-network.conf` = `[Unit] Wants=network-online.target After=network-online.target`. Without it, a problem found at boot (e.g. an SSD missing after a power cut) was emailed before DNS was up and silently dropped — msmtp has no queue. If the network never comes up they still start, after the ~2 min wait-online timeout. |

Tests:
```bash
echo "Test from wdbx-media" | sudo mail -s "T610 mail test" root
sudo zpool scrub wdbx                          # ZED emails when it finishes
sudo mdadm --monitor --scan --test --oneshot   # two test mails (name quirk, harmless)
sudo tail /var/log/msmtp.log                   # if anything doesn't arrive: look for EX_TEMPFAIL
```

Gmail had flagged the first test as spam; SPF/DKIM/DMARC all pass, and a filter now marks these as important with a WDBX label. Recipients need the same filter.

---

## 5. Power

The BMC power-restore policy is `always-on`, so the server boots by itself when power returns:
```bash
sudo ipmitool chassis status | grep -i "power restore"   # Power Restore Policy : always-on
```
Unclean power loss is safe for ZFS (copy-on-write); the md mirror may resync after boot.

---

## 6. When a Drive Fails — Runbook

All of these were rehearsed on 2026-11-09 (drills 1–3). Find the drive by serial (§1), not by `sdX`. To make a drive's bay light up: `sudo dd if=/dev/disk/by-id/<wwn> of=/dev/null bs=1M count=3000` (reads only — never swap `if` and `of`).

### Pool drive drops out, then comes back (drill 1)
1. Email arrives; `zpool status wdbx` shows `DEGRADED`, the drive `REMOVED`/`UNAVAIL`. Data stays readable from the other drive.
2. Reseat it. ZED normally brings it back by itself and resilvers only what changed; otherwise `sudo zpool online wdbx <wwn>`.
3. `sudo zpool scrub wdbx`; when it reports `repaired 0B … 0 errors`, `sudo zpool clear wdbx`.

### Pool drive dies — replace it (drill 2)
1. `sudo zpool offline wdbx <old-wwn>`, then swap the drive (same bay is fine; hot-swap works).
2. Test the new drive first if time allows (§7).
3. `sudo zpool replace wdbx <old-wwn> /dev/disk/by-id/<new-wwn>` — same drive in the same slot: `sudo zpool replace wdbx <wwn>`.
4. Watch `zpool status wdbx`: a temporary `replacing-N` entry, resilver progress, then a normal mirror. ~13 GB took under 2 minutes.
5. Scrub, as above.

If the replacement disk **was ever in a ZFS pool**, wiping its partition table is not enough — ZFS keeps four labels, two at each end of the partition, and refuses the disk with *"is part of active pool"* (cannot be forced with `-f`; `labelclear` also refuses). Zero both ends, with a serial check so the wrong disk can't be hit:
```bash
D=/dev/disk/by-id/<new-wwn>
if [ "$(lsblk -dno SERIAL $D)" = "<expected serial>" ]; then
  MB=$(( $(sudo blockdev --getsize64 $D) / 1048576 ))
  sudo dd if=/dev/zero of=$D bs=1M count=32 oflag=direct
  sudo dd if=/dev/zero of=$D bs=1M count=32 seek=$(( MB - 32 )) oflag=direct
  sudo blockdev --rereadpt $D
fi
```
After that, *"contains a corrupt primary EFI label"* is safe to override with `zpool replace -f`.

### OS SSD drops out while running (drill 3A)
1. Email arrives; `cat /proc/mdstat` shows `[2/1] [U_]`. The server keeps running.
2. Reseat it, find its name: `lsblk -o NAME,SIZE,SERIAL | grep -A3 <serial>`.
3. `sudo mdadm /dev/md0 --re-add /dev/<name><N>` where **N = 1 for `…070`, 2 for `…067`** (use `--add` if `--re-add` is refused; a full resync of 46 GB takes ~6 minutes). Done when `mdstat` shows `[UU]`.

### OS SSD missing at boot (drill 3B)
Verified with each SSD removed in turn: the server boots from the other one with no prompt, and the degraded-array email arrives once the network is up. Reinsert the SSD (hot) and re-add as above.

If it ever stops at "No boot device available", press Ctrl+C at the LSI banner and set the remaining SSD as the boot device. If it stops at an `(initramfs)` prompt: `mdadm --assemble --scan --run`, then `exit`.

### OS SSD dies — replace it *(not drilled)*
The new SSD needs the surviving SSD's partition layout and its own copy of GRUB:
```bash
sudo sgdisk -R /dev/<new> /dev/<surviving>   # copy the partition table…
sudo sgdisk -G /dev/<new>                    # …with fresh GUIDs
sudo mdadm /dev/md0 --add /dev/<new><N>      # N = the ~46 GB partition on the new disk
sudo parted /dev/<new> disk_set pmbr_boot on
sudo dpkg-reconfigure grub-pc                # tick both SSDs
```
Then check the LSI Boot/Alt devices (Ctrl+C at POST) include the new SSD. It must be at least 46.6 GB.

---

## 7. Testing a Drive Before Use

Every donated drive gets a full write-and-read pass plus a SMART comparison. "Fine in Disk Utility" is not a test — that check passed a 2 TB drive that turned out to be dying. The pass also wipes the donor's data.

```bash
mkdir -p ~/burnin
d=sdX; serial=$(lsblk -dno SERIAL /dev/$d)        # check the serial before going on
sudo smartctl -a /dev/$d > ~/burnin/$serial.smart-before.txt
sudo systemd-run --unit="burnin-$serial" \
  -p StandardOutput="file:/home/wdbx/burnin/$serial.log" -p StandardError=inherit \
  badblocks -b 4096 -wsv -t 0 -o "/home/wdbx/burnin/$serial.bad" "/dev/$d"
```
**Destroys everything on the drive.** ~11 h for 3 TB, ~7–8 h for 2 TB; drives can run in parallel. `systemctl list-units 'burnin-*'` shows what's running.

When the log says `Pass completed, 0 bad blocks found`, compare `sudo smartctl -A /dev/$d` with the before-snapshot. Pass: pending and uncorrectable at 0, reallocated barely moved, CRC not rising (a rising CRC count means the bay/connection, not the disk). SMART `FAILED` or a reallocated count at its threshold means don't use it.

---

## 8. Remote Access — Tailscale

Tailnet owned by the station manager's Gmail account; the developer's personal account (metaphorever) is an admin. Policy has:
```
"tagOwners": { "tag:server": ["autogroup:admin"] },
```

| Node | Tailscale IP | Notes |
|------|--------------|-------|
| `wdbx-media` (T610) | 100.80.149.81 | `tag:server`; runs with `--accept-routes` |
| `wdbx-stream2-3020` | 100.91.154.86 | `tag:server`; **subnet router for `192.168.2.0/24`** (station LAN, so the NAS is reachable from anywhere on the tailnet) |
| `sarah-pc` | 100.82.221.77 | developer's home PC, personal, untagged |

Tagged devices are owned by the tailnet (not a person) and don't expire. Personal devices stay untagged. The developer's work Mac Mini is still to be added (untagged).

From any tailnet device: `ssh wdbx@wdbx-media`.

Measured through the subnet route: ~5 ms ping to the NAS, ~3.7 MB/s throughput. Bulk copies (terabytes) belong on the station LAN, not over Tailscale.

---

## 9. Build Gotchas — Read Before Rebuilding

1. **"Invalid PCIe card found in the Internal Storage slot! System Halted!"** The T610's dedicated storage slot only accepts whitelisted Dell controllers; a cross-flashed H310 reports as LSI. Put the HBA in a regular rear PCIe slot. The existing SFF-8087 backplane cables reached slot 2.
2. **USB boot looks unsupported.** It isn't — on this generation USB sticks appear under **"Hard drive C:"** in F11 / F2 → Boot Settings → Hard-Disk Drive Sequence, not as a USB entry. Ventoy works.
3. **Old H700 RAID metadata hijacks disks.** The installer assembled the SSDs into a phantom DDF container (`md126`/`md127`), Reformat was greyed out and deleting crashed it. Fix from the installer shell: `mdadm --stop /dev/md126 /dev/md127`, `wipefs -a` each SSD, reboot.
4. **Build the OS RAID1 from partitions, not whole disks:** "Use As Boot Device" on SSD 1, "Add As Another Boot Device" on SSD 2, an unformatted partition on each, RAID1 from those, ext4 `/` on the md device.
5. **"No boot device available" after a successful install.** Three fixes were needed: set Boot/Alt devices to the two SSDs in the LSI utility (Ctrl+C at POST); `parted /dev/sdX disk_set pmbr_boot on` on both SSDs (old Dell BIOS skips GPT disks without it); GRUB had only been written to one SSD — install on both.
6. **No network after install:** the NIC had no cable during install, so netplan had no config. Write `/etc/netplan/01-wdbx.yaml` by hand. YAML reported "inconsistent indentation" when the real problem was a missing `:` after the interface name — check with `sudo cat -A`.
7. **`zpool create` fails with "failed to detect device partitions … 19".** The ST600MP0036 drives were formatted with **T10 Protection Information type 2** (dmesg: `Read(32)`, "Logical block reference tag check failed"). Fix: `sg_format --format --fmtpinfo=0 --early /dev/sdX` (sg3-utils, ~1–2 h, drives format in parallel), reboot, confirm `sg_readcap --long` shows `prot_en=0`. Expect this on any used enterprise SAS drive.
8. **Mail silently not sending:** needs `msmtp-mta` (for `/usr/sbin/sendmail`) **and** `bsd-mailx` (for `mail`, used by ZED and smartd), not just `msmtp`.
9. **`sudo … | tail -1` hides the sudo password prompt** and looks like a hang. Run `sudo -v` first.
10. **CIFS read-ahead wastes the WAN.** Reading 64 KB chunks off the NAS over Tailscale pulled ~18× more data than requested. Mount with `cache=none` for small-read workloads (halved the quick-hash time).
11. **Long jobs die with the SSH session.** `badblocks` installs its own hangup handler, so `nohup` doesn't protect it — a dropped connection killed three 11-hour tests at 2 h 16 m. Run long jobs with `systemd-run` (or inside `tmux`).
12. **Drive names and bay labels lie.** `sdX` names change on reseat (a wrong drive got pulled once because of it) and the front bay labels don't match the controller's slot numbers. Go by serial and wwn.
13. **Reusing a former ZFS disk** needs both ends zeroed (§6), not just `wipefs`.
14. **Boot-time alerts were silently dropped** until the monitors were ordered after `network-online.target` (§4).

---

## 10. Next Steps

1. Test the NAS's idle 2 TB spare in bay 3; add the 2 TB pair to `wdbx`.
2. Folder triage and ingest upgrades for the old material ([`nas_consolidation.md`](nas_consolidation.md)).
3. Deploy the toolbox on the T610 in test mode with download jobs **off** (the live instance on `wdbx-stream2-3020` must stay the only downloader until cutover). Install with the pinned `requirements.txt` (`sqlmodel<0.0.45`).
4. Move to the station: DHCP reservation, test from the studio PC, then full-hash verification and the canonical copy over the LAN.
5. Before retiring the NAS: replace the Hitachi (≥ 3 TB), add the NAS's 2 TB drives as pairs, and move backups to ZFS snapshots + `zfs send` (the current NAS backup job copies new files only and can't follow moves).
6. Physically destroy the failed WD drive.
