# X-Ray Annotation Guidelines — Labeling Rules per Class

**Version:** 1.0  
**Audience:** Expert annotators and domain specialists (customs inspectors, radiologists with X-ray experience).  
**Principle:** When in doubt, do NOT label. A missing label is recoverable; a wrong label corrupts the model silently.

---

## 0. General Rules (Apply to Every Class)

### Box placement
- Draw the **tightest possible axis-aligned rectangle** that fully contains the object.
- The box must not clip the object at any edge. Add ≤ 5 px of padding if the boundary is ambiguous.
- Do **not** include the entire bag or container — only the specific region of interest.
- Minimum box size: **32 × 32 px** for most classes (see per-class rules below). Boxes smaller than this indicate either a resolution problem or an annotation error.

### Dual-energy scans
- When both high-energy and low-energy channels are available, annotate **both frames** with the same bounding box coordinates.
- Use the high-energy channel as the primary reference for box placement.
- Note any material-discrimination signal (orange = organic, green = metal/inorganic) in the `note_uz` field.

### Confidence
- Only label what you can **identify with certainty**. Ambiguous objects belong in `ORGANIC_ANOMALY` or `METALLIC_ANOMALY`, not in a specific class.
- If you cannot distinguish between two categories, annotate the more specific one **only** if you are >80% certain. Otherwise use the anomaly class.

### Inter-annotator agreement
- All labels for the same scan must agree within **±10 px** on box edges.
- If two annotators disagree on category, the scan is flagged for a third expert review — do not resolve by majority vote without review.
- `RECLASSIFIED` feedback always triggers re-review by a second annotator before the label enters `REVIEWED` status.

### What NOT to label
- X-ray calibration phantoms or test objects.
- Operator hands, belt buckles, or scanner mechanical artifacts.
- Known benign organic masses (food, clothing) unless they are unusually dense and require flagging as `ORGANIC_ANOMALY`.

---

## 1. NARCOTICS

**Minimum labeled examples required before training: 1 500**  
**Minimum box size: 32 × 32 px**

### Visual characteristics in X-ray
- **Powder** (heroin, cocaine, fentanyl): uniform grey mass, often in rectangular or cylindrical shapes (brick, balloon, condom). Low-to-medium density organic signal.
- **Pressed tablets**: high-density, geometrically uniform. Round or rectangular grid patterns.
- **Liquid** (GHB, methamphetamine solution): irregular soft container shape, very low X-ray attenuation.
- **Cannabis**: very low-density, heterogeneous organic; often vacuum-packed (hard edges on a soft mass).
- **Concealment methods** (body packing, double-wall containers): abnormal thickness or layering artifact.

### When to label
- Label every distinct package as a **separate box** if they are visually separated by > 10 px.
- A single "brick" wrapped in multiple layers = one box covering the entire multi-layer structure.
- If you cannot distinguish narcotics from food (e.g., rice, compressed tea), use `ORGANIC_ANOMALY` and note the suspicion.

### When NOT to label as NARCOTICS
- Clothing compressed to high density.
- Food items with normal organic density profile.
- Body parts visible through clothing (shoulder, hip joint).

---

## 2. FIREARM

**Minimum labeled examples required before training: 800**  
**Minimum box size: 45 × 45 px** (firearms have recognisable shapes; smaller boxes are noise)

### Visual characteristics
- **Pistols/revolvers**: distinctive silhouette — barrel, trigger guard, grip. High-density metallic throughout.
- **Rifles/shotguns**: long barrel, stock (may be lower density if wood/polymer), metallic action.
- **Handgun components** (slides, barrels, frames): label each component separately if disassembled and individually recognisable; label as single box if stacked.
- Semi-automatic pistols often show a characteristic "squarish" body + protruding barrel.

### When to label
- Disassembled firearms: label each component that is independently identifiable as a firearm part.
- Imitation/replica firearms: label as FIREARM unless you are certain it is non-functional (e.g., obvious toy scale). When uncertain, label.
- 3D-printed firearms: polymeric density but firearm shape — label as FIREARM and add `"material":"organic_polymer"` in attributes.

### When NOT to label
- Power tools (drill, angle grinder) — metallic, elongated, but different silhouette.
- Umbrella or cane with metallic shaft.

---

## 3. BLADED_WEAPON

**Minimum labeled examples required before training: 600**  
**Minimum box size: 30 × 30 px** (note: blades are often thin and elongated)

### Visual characteristics
- Knives: thin, elongated high-density metallic object. Blade + handle density difference often visible.
- Swords, machetes: same but longer; box may be large.
- Box cutters, razors: very thin, short metallic strip.
- Ceramic blades: low-density, almost invisible — only label if shape is unmistakable.

### Special rules
- Kitchen knives in checked luggage: label if > 10 cm estimated blade length. Annotate as `bladed_weapon` with note `"context":"culinary"` — the model must detect, the operator decides.
- Concealed blades (sewn into clothing, taped to book spine): label the blade, note the concealment method.
- Aspect ratio of blade boxes is often extreme (> 5:1 length-to-width). This is expected and valid — do not reject these boxes.

---

## 4. EXPLOSIVE

**Minimum labeled examples required before training: 1 200**  
**Minimum box size: 32 × 32 px**

### Visual characteristics
- **IED components**: unusual wiring harnesses, detonators (small metallic cylinders), timer circuits (PCB + battery).
- **PETN/RDX blocks**: uniform, medium-density organic mass, often rectangular.
- **Pipe bombs**: metallic tube with end caps and possible wiring.
- **TATP/peroxide-based**: very low X-ray attenuation (nearly invisible) — label based on container shape and context.
- **Commercial explosives** (mining, construction): uniform blocks, often marked with wires.

### When to label
- Always label the primary charge + any detonation system as **two separate boxes** if both are visible.
- If only wire/detonator is visible without a charge, label as `EXPLOSIVE` and note `"component":"initiator_only"`.

### Critical rule
- **When uncertain between EXPLOSIVE and ORGANIC_ANOMALY, choose ORGANIC_ANOMALY and escalate.** Never dismiss an EXPLOSIVE suspicion — escalation is cheap; a miss is catastrophic.

---

## 5. CURRENCY

**Minimum labeled examples required before training: 500**  
**Minimum box size: 25 × 25 px**

### Visual characteristics
- Banknotes in bulk: layered, uniform, medium-density organic. Often banded (rubber band = thin dark stripe).
- Coins: high-density metallic discs. Often stacked (cylindrical profile).
- Mixed cash (coins + notes): annotate as one box if interleaved, or two separate boxes if clearly separated.

### Thresholds (customs reporting thresholds — annotate all, report ≥ threshold)
- Label all visible currency bundles regardless of estimated amount.
- Note estimated denomination range in `note_uz` if visible (e.g., "appears to be large-denomination notes").

---

## 6. ORGANIC_ANOMALY

**Minimum labeled examples required before training: 400**  
**Minimum box size: 64 × 64 px** (amorphous; small boxes are not useful)

### Use when
- An organic mass (orange/yellow on dual-energy) is denser, more uniform, or shaped differently from normal luggage contents.
- You suspect narcotics, explosives, or biological material but cannot confirm category.
- A body-packing suspicion exists but not enough density detail to confirm.

### Do not use for
- Normal food, clothing, or toiletry bottles with expected density.
- The entire luggage contents — only suspicious sub-regions.

---

## 7. METALLIC_ANOMALY

**Minimum labeled examples required before training: 400**  
**Minimum box size: 64 × 64 px**

### Use when
- A metallic mass (green on dual-energy) is anomalous in shape, density, or placement but does not match any firearm or bladed weapon profile.
- Possible hidden compartment with metallic lining.
- Unidentifiable electronic assembly that could be a weapon component.

### Do not use for
- Obvious laptops, phones, cameras, keys.
- Normal tools with recognised shapes (screwdrivers, wrenches) — unless concealed in an unusual way.

---

## 8. CONTRABAND_OTHER

**Minimum labeled examples required before training: 300**  
**Minimum box size: 25 × 25 px**

### Use when
- The object is clearly contraband (endangered species, CITES-protected items, counterfeit goods) but does not fit any category above.
- Annotate with a specific description in `note_uz` — this class is the least informative and future versions of the taxonomy will split it.

---

## 9. Inter-Annotator Agreement Protocol

| Disagreement type | Resolution |
|---|---|
| Box boundary ≤ 10 px difference | Accept the tighter box |
| Box boundary > 10 px difference | Flag for third-annotator review |
| Category disagreement | Third-annotator review; escalate to supervisor if unresolved |
| RECLASSIFIED feedback | Mandatory second review before REVIEWED status |
| EXPLOSIVE vs. ORGANIC_ANOMALY | Always prefer ORGANIC_ANOMALY + escalation note |

---

## 10. Data Volume Reality Check

The table below answers "can we start training this class yet?"  
These are **minimums**, not targets. Training below minimum produces a detector that recognises the training artefacts (specific bag colors, orientations, packaging from the last shift), not the threat class.

| Class | Minimum | Realistic usable target | Notes |
|---|---|---|---|
| NARCOTICS | 1 500 | 5 000+ | Extreme intra-class variance; most critical class |
| FIREARM | 800 | 2 500+ | Many form factors; disassembled firearms add complexity |
| BLADED_WEAPON | 600 | 1 500+ | Orientation sensitivity; needs both views |
| EXPLOSIVE | 1 200 | 4 000+ | Rare in training data; collect from every seizure |
| CURRENCY | 500 | 1 200+ | More consistent visually |
| ORGANIC_ANOMALY | 400 | 1 000+ | Catch-all; use to reduce false positives |
| METALLIC_ANOMALY | 400 | 1 000+ | Same |
| CONTRABAND_OTHER | 300 | 800+ | Will be split into sub-classes |

**Push back on "just train it a little more":**  
Training a class with 50 examples and adjusting loss weights is not equivalent to having 500 examples. The model will not generalise. The only fix for data below minimum is more data collection and annotation.

---

## 11. Feedback Quality Signals to Monitor

| Signal | Action if anomalous |
|---|---|
| High RECLASSIFIED rate for one operator | Calibration session required |
| High REJECTED rate (FP) in one class | Review model confidence threshold; may need more hard negatives |
| High MISSED rate (FN) in one class | Critical: model is blind to this class; prioritise data collection and retrain |
| Same scan annotated differently by two operators | Flag for adjudication; do not average |
