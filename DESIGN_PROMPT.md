# Design Prompt — X-ray Customs Operator Console

> Copy everything below the line into Claude (or Claude in Figma) to generate / redesign the operator console UI.
> It is grounded in the real data contracts, workflow, and domain of this project.

---

## ROLE

You are a senior product designer specializing in **high-stakes operational tooling** (air-traffic control, medical imaging, security screening). Design the UI for an **on-premise, air-gapped X-ray decision-support console** used by customs operators at border lanes. The operator is the **sole decision-maker**; the system only advises. Every screen must reinforce *human-in-command*, never auto-enforce.

Deliver: a **dark-mode, glassmorphic, single-operator workspace** that an operator can read at a glance under fluorescent lighting, across an 8-hour shift, without fatigue or ambiguity.

## NON-NEGOTIABLE PRINCIPLES (these shape the visual language)

1. **Decision-support only.** The machine describes; the operator decides. Never style any AI output as a verdict, command, or approval. Always pair AI output with the disclaimer: *"Bu xulosa faqat ma'lumotnoma uchun. Qaror operatorga tegishli."* (This conclusion is advisory only. The decision belongs to the operator.)
2. **Fail-closed / conservative.** Absence of a threat is **never** styled as "all clear / safe to pass." `YO'Q` (not detected) ≠ clearance. A missing or failed analysis must look *alarming or neutral*, never reassuring-green.
3. **Conflict is visible.** If the operator clears a scan the system rated **HIGH risk**, surface a loud, deliberate confirmation — the operator is consciously overriding the signal.
4. **One decision path.** There is exactly one place to commit a decision. No hidden or duplicate decision affordances.
5. **Audit & provenance always reachable.** Model name + version, image hash, and an append-only audit trail must be one click away.
6. **Uzbek-first.** All operator-facing copy is **Uzbek (Latin script, `uz-Latn`)** by default, with `uz-Cyrl` and `ru` as locale fallbacks.

## USERS & CONTEXT

- **Primary user:** customs X-ray operator at a single lane, watching a queue of scans (vehicles, cargo, baggage, parcels).
- **Environment:** fixed desktop, large monitor, mouse + keyboard, occasionally touch. Air-gapped LAN, no internet.
- **Cognitive load:** operator scans many items fast; must instantly spot the *one* that needs physical inspection. Speed + zero false reassurance.

## THE WORKFLOW TO SUPPORT (left → right reading order)

1. **Triage the queue** — pick the next scan; high-risk floats to the top, color-coded.
2. **Inspect the imagery** — view X-ray frame(s), zoom/pan, see detector bounding boxes overlaid.
3. **Read the analysis** — per-detection cards (category, confidence, VLM rationale) + an overall plain-Uzbek summary.
4. **Judge each detection** — confirm / reject / reclassify; draw boxes for items the detector *missed*.
5. **Commit one outcome** — clear / inspect / seize / escalate, with mandatory note for seizures and a warning when overriding a HIGH signal.
6. **Confirmation** — decision logged to the audit trail and active-learning loop.

## SCREEN INVENTORY

Design these screens/states:

### A. Login
Minimal centered card: username + password, error state for failed auth. Same dark glass aesthetic. Project title: **"Rentgen nazorat tizimi"** (X-ray Monitoring System).

### B. Main Operator Workspace — 3-column layout

**Top chrome bar (full width):**
- Left: `ScanLine` icon + app title "Rentgen nazorat tizimi"
- Center/right: lane id ("Yo'lak: lane-1"), live connection indicator, operator name ("Operator: admin"), digital clock (HH:MM:SS), audit-log toggle, logout.

**Left column — Scan Queue (~256px fixed):**
- Header "Navbat" (Queue) + refresh.
- Primary action button (teal gradient): **"Rentgen ko'rish"** (Take X-ray).
- Acquisition mode toggle: **"Kamera"** (live camera) vs **"Rasm yuklash"** (image upload).
- Filter tabs: **"Ochiq"** (open) · **"Barcha"** (all) · **"Tugallangan"** (done).
- Scrollable scan rows. Each row: subject type, lane id, **risk badge** (icon + label + color), time (HH:MM), truncated scan id (mono). Selected row: accent left-border + glow. HIGH-risk rows: red left-border, red halo, floated above the rest.

**Center column — Verdict / Inspection (flex, scrollable):**
- **Risk banner** at top: large color-coded pill for the overall risk band + summary.
- Metadata strip: subject, modality, lane id, scan id (mono, truncated).
- **Two sub-columns:**
  - *Left (flex):* **X-ray viewer** — canvas image with frame tabs ("Kadr 1", "Kadr 2", or `view_label` like "high_energy"/"side"/"top"). Detection boxes drawn as SVG overlays, stroke colored by category. Zoom controls (1×–4×). **Draw mode** (pencil): operator drags to add a *missed* region box, then picks category + optional note. Selected detection box thickens + glows. Cursor → crosshair in draw mode.
  - *Right (~288px, scroll):*
    - **"Aniqlangan buyumlar"** (Detected items) + count badge. List of **Detection cards**, sorted by severity then score. Each card: category name (colored), optional judgement badge, expand chevron; native label (mono); two confidence bars (detector score + VLM confidence); VLM rationale excerpt (italic); expand → pixel box/size + detector attributes; judgement controls — **"Tasdiqlash"** (confirm, green) / **"Rad etish"** (reject, red) / **"Qayta tasniflash"** (reclassify, amber + category dropdown). Low-confidence (<0.45) cards: dimmed (opacity ~75%).
    - **"Tizim xulosasi"** (System conclusion): the Uzbek summary, model name+version, and the advisory disclaimer. States: *"Xulosa tayyorlanmoqda…"* (preparing, pulsing) / *"Xulosa mavjud emas"* (unavailable).
    - **"O'tkazib yuborilgan buyumlar"** (Missed items): operator-drawn boxes (amber surface), each with category, pixel coords, note, delete (×).

**Right column — Decision (~320px, fixed, scrollable):**
- Eyebrow "Operator" + `Gavel` icon + **"Operator qarori"** (Operator decision).
- **Outcome selector (2×2 grid):**
  - **"Qo'lda tekshirishga yuborish"** — inspect (blue)
  - **"Operator qarori bilan o'tkazish"** — clear (green)
  - **"Buyumni musodara qilish"** — seize (red)
  - **"Yuqori instansiyaga yuborish"** — escalate (amber)
  - Active outcome: ring + stronger glow.
- **Notes textarea**: "Izoh (ixtiyoriy)" — but **required (red ` *`)** when outcome = seize. Placeholder: "Topilgan buyumlar yoki boshqa kuzatuvlar…".
- **Confirmation prompt** (conditional): for *clear on a HIGH-risk scan* → red "Diqqat" warning; for *seize* → amber "Tasdiqlashmi?". Yes/No.
- **Submit** (indigo gradient): "Qarorni saqlash". Disabled until outcome chosen (and note present for seize). Saving spinner → "Saqlanmoqda…".
- **Done state**: `CheckCircle2` + "Qaror allaqachon qilindi" + timestamp.

### C. Audit Log (slide-over / panel)
"Audit izlari" — vertical timeline of events (`acquisition_recorded → detection_recorded → verdict_recorded → feedback_recorded`), each with event type, operator id, payload summary, timestamp, and a chain-of-custody validity indicator (HMAC verified).

### D. Image Screening (upload mode)
Drag-drop 1..N X-ray JPEG/PNG. Per-image result card: filename, `wagon_type`, `main_cargo`, **flags** (narcotics / weapon / tobacco / other — each `BOR` = present / `SHUBHALI` = suspected / `YO'Q` = not detected), risk band, Uzbek summary, inference time. Remember: `YO'Q` is **not** a green "safe" — keep it neutral.

### E. High-Risk Alert Banner (floating, persistent)
On a `scan.flagged` high-risk push: red/amber bar with icon + "Diqqat: yuqori xavf darajasi aniqlandi" + subject/scan-id/time + sound toggle + "Ko'rish" (view) + dismiss.

## RISK BANDS (queue triage — advisory only)

| Band | Uzbek label | Color intent |
|------|-------------|--------------|
| `clear`  | "Shubhali buyum aniqlanmadi" | neutral (NOT a reassuring green) |
| `low`    | "Past xavf darajasi"          | blue |
| `medium` | "O'rtacha xavf darajasi"      | amber/orange |
| `high`   | "Yuqori xavf darajasi"        | red, with halo/glow |

## THREAT CATEGORIES → COLOR LANGUAGE

`firearm`, `explosive` → **red** · `bladed_weapon` → **orange** · `narcotics` → **purple** · `currency` → **yellow** · `organic_anomaly` → **cyan** · `metallic_anomaly` → **slate** · `contraband_other` / `unknown` → **neutral gray**. Each maps to a severity rank (explosive/firearm/bladed highest) used for sorting and glow intensity.

## VISUAL SYSTEM

- **Theme:** dark mode. Background = deep slate (`#0f172a`–`#1a1d27`). Surfaces = translucent glass: `bg-white/5 backdrop-blur-sm border-white/10` (and a stronger `bg-white/10 backdrop-blur-lg border-white/20` for emphasis).
- **Depth:** layered elevation shadows; colored glows for risk (red high / amber medium / steel neutral); red "halo" for critical alerts.
- **Typography:** bold tracking-tight headings; tiny uppercase tracked-out section eyebrows in muted tone; relaxed `text-sm` body; **monospace for ids, hashes, pixel coordinates**.
- **Icons:** Lucide (ScanLine, ShieldAlert, AlertTriangle, Info, Layers, Inbox, Gavel, CheckCircle2, Video, ImageUp, Pencil, Loader2…).
- **Motion:** restrained — pulse for "analyzing", subtle glow transitions on selection, spinner on save. No decorative animation.
- **Accessibility:** strong contrast, ARIA labels, screen-reader announcements for new high-risk alerts, keyboard navigable queue + judgement controls. Color is never the *only* signal — always pair with icon + text.

## STATES TO DESIGN EXPLICITLY (don't show only the happy path)

- Queue empty ("navbat bo'sh").
- Scan **pending / analyzing** (detector running) and **verdict preparing** (VLM running) — pulsing placeholders.
- Detection **failed** (status `failed`) — neutral/alarming, never green; show error.
- **completed_no_findings** — neutral, with explicit "not a clearance" framing.
- VLM **unavailable** / hallucination rejected (the 502 case) — operator sees *nothing* rather than an unverifiable verdict; explain why.
- Decision **already made** (locked, read-only).
- Connection **lost** (WebSocket down) indicator.

## DELIVERABLES

1. High-fidelity mockup of the **main 3-column workspace** (a HIGH-risk scan with 2–3 detections, one expanded card, VLM summary present, decision panel mid-interaction).
2. The **HIGH-risk override confirmation** moment.
3. The **image-screening upload** view with mixed `BOR`/`SHUBHALI`/`YO'Q` results.
4. The **audit log** timeline.
5. A **component/style sheet**: risk badges, detection card, outcome buttons, glass surfaces, color + type tokens.

Keep it calm, dense, and unambiguous. The operator's eye should land on risk first, imagery second, decision last — and never be told by the UI that something is "safe."
