# T610 Archive Server — Build & Operations
*Last updated: 2026-10-18*

The donated Dell PowerEdge T610 is the planned new home for the archive and the toolbox, replacing the aging Iomega px6-300d NAS as primary storage. Streaming stays on the redundant Ubuntu stream PCs.

**Status:** OS, storage, alerting, power policy and Tailscale are done. The box is still off-site at the developer's home, on the home LAN. The live system (`wdbx-stream2-3020` + NAS) keeps running unchanged; toolbox dev/testing will happen on the T610. Current work: sizing the de-duplicated archive — see [`nas_consolidation.md`](nas_consolidation.md).

---

## 1. Hardware

| Component | Detail |
|-----------|--------|
| Server | Dell PowerEdge T610, BIOS 6.4.0, service tag `58C8VR1` |
| CPU / RAM | 2× Xeon 5600-series (Westmere), ECC DDR3 |
| Storage controller | Dell PERC H310 cross-flashed to LSI 9211-8i **IT mode** (FW 20.00.07.00-IT, BIOS 7.39.02.00), in rear PCIe slot 2 (x8). Bought pre-flashed from StorageTekPro. |
| Removed | Original PERC H700 (hardware RAID, cannot be flashed to IT mode) + its battery — kept in case |
| OS disks | 2× Samsung MZ-5S70500-0D3 46 GB SATA SSD, backplane slots 2–3 |
| Data disks (test) | 6× Seagate ST600MP0036 600 GB 15k SAS, backplane slots 0, 1, 4–7. SMART OK, 0 grown defects, 4k–35k power-on hours |
| NIC | Broadcom BCM5709 dual GbE (`eno1` cabled, `eno2` unused) |
| Management | iDRAC6 (showed `192.168.0.120` at POST on the home LAN) |

All 8 bays are full. There is no spare bay for an extra drive.

---

## 2. OS Layout

| Item | Detail |
|------|--------|
| OS | Ubuntu Server 24.04.5 LTS |
| Hostname / user | `wdbx-media` / `wdbx` (matches the runbook's `/home/wdbx/wdbx-toolbox` paths) |
| Timezone | `America/Chicago` |
| Root | mdadm RAID1 `md0` (`sda1` + `sdb2`), ext4 on `md0p1` mounted at `/`. UUID `7767cbbe:5afd57a6:185cc2cc:a05b3f7d` |
| Boot | Legacy BIOS. 1 MB `bios_grub` partition on each SSD; GRUB installed on **both** (`grub-pc/install_devices` lists both SSDs), so either SSD boots alone |
| Network | `/etc/netplan/01-wdbx.yaml`, DHCP on `eno1` and `eno2` (`optional: true`). Home LAN address `192.168.0.177` for now — needs a DHCP reservation at the station |

Check the mirror: `cat /proc/mdstat` should show `[UU]`.

---

## 3. ZFS Pool

`testpool` — **temporary**, built on the 6 SAS drives to prove out the stack:

```
raidz2 of the six SAS drives, by /dev/disk/by-id/wwn-* names
ashift=12, compression=lz4, atime=off, xattr=sa, acltype=posixacl, recordsize=1M
mountpoint=/srv/wdbx, ~2.11 TiB usable (~2.3 TB)
```

- Final plan: once the curated archive is verified on the T610, retire the NAS and rebuild the pool on its 6× 2 TB drives (raidz2, ~8 TB usable), **reusing the `/srv/wdbx` mountpoint** so nothing pointing at it changes. Note ZFS 2.2.2 cannot expand a raidz vdev; growth means a new pool + `zfs send`.
- Planned datasets: `testpool/{archive,overnight,staging}` → `/srv/wdbx/{archive,overnight,staging}`, mapping to the toolbox's `nas.archive_path`, `nas.overnight_output_path` and `local_staging.path`.
- Scrub: Ubuntu's `zfsutils-linux` cron scrubs monthly; ZED emails the result.

---

## 4. Alerting

All local alerts go out by email through the same DreamHost account the toolbox uses.

| Piece | Config |
|-------|--------|
| Mail transport | `msmtp` + `msmtp-mta` (provides `/usr/sbin/sendmail`) + `bsd-mailx` (provides `mail`). Config `/etc/msmtprc` (root, chmod 600): `smtp.dreamhost.com:587`, TLS, logfile `/var/log/msmtp.log`. msmtp AppArmor profile left **disabled** (it can block `/etc/aliases` and the logfile) |
| Recipients | `/etc/aliases` maps `root` and `default` to the alert recipients |
| ZFS | `/etc/zfs/zed.d/zed.rc`: `ZED_EMAIL_ADDR="root"`, `ZED_NOTIFY_VERBOSE=1` (scrub reports arrive, so a monthly email proves the path works) |
| RAID mirror | mdadm's default `MAILADDR root`. Only failures/degradation/test messages are emailed |
| Drive health | `/etc/smartd.conf`: `DEVICESCAN -d removable -n standby -a -m root -s (S/../.././02\|L/../../6/03) -M exec /usr/share/smartmontools/smartd-runner` — short self-test daily 02:00, long Saturdays 03:00 |

Tests:
```bash
echo "Test from wdbx-media" | sudo mail -s "T610 mail test" root
sudo zpool scrub testpool                      # ZED emails when it finishes
sudo mdadm --monitor --scan --test --oneshot   # two test mails (name quirk, harmless)
sudo tail /var/log/msmtp.log                   # if anything doesn't arrive
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

## 6. Remote Access — Tailscale

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

## 7. Build Gotchas — Read Before Rebuilding

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

---

## 8. Next Steps

1. Finish sizing the de-duplicated archive ([`nas_consolidation.md`](nas_consolidation.md)).
2. Create the `testpool/{archive,overnight,staging}` datasets.
3. Deploy the toolbox on the T610 in test mode with download jobs **off** (the live instance on `wdbx-stream2-3020` must stay the only downloader until cutover).
4. Move to the station: DHCP reservation, test from the studio PC, then full-hash verification and the canonical copy over the LAN.
5. Retire the NAS; rebuild the pool on its 6× 2 TB drives.
