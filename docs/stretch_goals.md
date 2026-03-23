# WDBX Radio Toolbox — Stretch Goals & Future Ideas
*Parking lot for ideas that are out of scope for v1 but worth revisiting later.*
*Add to this freely. Nothing here is a commitment.*

---

## Composer Tools

- **Prerecorded show composer** — build an overnight show from scratch using the processed archive library. Select a theme, genre, era, or DJ, and the tool assembles a full 2-hour block from approved segments, with proper station IDs and padding, ready to drop into the schedule. Essentially a playlist builder with broadcast-ready output.
- **Themed block generator** — "Best of Blues Month," "Summer Classics," etc. Tag-driven selection from the evergreen library with human curation step before output.

---

## Scheduler Integration

- **Overnight programming calendar** — visual schedule of what processed reruns are queued for which TBA/overnight slots. Drag-and-drop assignment. Tracks what has already aired to avoid repeating the same show too soon.
- **Automation feed output** — export the overnight queue in a format compatible with common broadcast automation software (if the station uses any).

---

## DJ & Volunteer Tools

- **DJ show prep assistant** — given a show's history and the music library, suggest tracks, pacing, and segment timing. Very aspirational.
- **Volunteer coordination** — track who has screened what, gamify the screening queue (leaderboard for most shows approved), send DJs a nudge when their show is in the queue.
- **Show notes / setlist capture** — simple form DJs fill out after their show (songs played, topics discussed, guests). Enriches the evergreen screening data without requiring transcription.

---

## Archive & Discovery

- **Public-facing archive browser** — a read-only web UI the station could share publicly for shows that are legally archivable long-term. Separate from the internal toolbox.
- **"Best of" detection** — identify episodes of a show that scored highest on evergreen metrics and surface them as recommended listens or rerun candidates.
- **Cross-show guest detection** — identify when the same voice (via speaker diarization) appears on multiple shows. Useful for finding collaborations and cross-promotion opportunities.

---

## Related Pain Points to Investigate

*Ask the station manager about these — they may reveal adjacent tools worth building.*

- How are live shows currently logged? Is there a show log or only the archive?
- Is there a music scheduling or logging system (e.g., for ASCAP/BMI reporting)? If not, could one be built from what we already capture?
- How are underwriting spots currently sold and tracked? Could the toolbox help manage the underwriting inventory?
- Is there a system for managing volunteer schedules and show assignments, or is that done manually?
- What does the station use for membership/pledge drive management? Any integration opportunity?

---

## Technical Stretch Goals

- **Pacifica API contribution** — if a proper API doesn't exist, could WDBX work with Otis to help design or document one that benefits all Pacifica affiliates?
- **Multi-station support** — generalize the toolbox for other community radio stations running on Pacifica infrastructure. Potentially a small open-source project.
- **Mobile app** — if the mobile web screening queue proves heavily used, a native app wrapper (PWA or simple React Native shell) for better audio playback control.
- **Whisper-powered search** — once transcripts exist, full-text search across the entire archive. "Find every episode where [topic] was mentioned."

---

*Last updated: planning session, March 2026*
*Revisit after v1 phases 1–3 are stable.*
