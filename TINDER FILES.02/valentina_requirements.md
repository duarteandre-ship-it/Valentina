# Valentina – AI Dating Assistant  
## Critical Design Prototype – Requirements Document

---

# 1. 🧠 Product Definition

## 1.1 Overview
**Valentina** is a **critical design / exhibition prototype** presented as a wearable AI dating assistant for blind users.

The system:
- Simulates assistive technology  
- Intentionally subverts usefulness through a satirical AI personality
- Creates a performative social experience, not a functional accessibility tool

---

## 1.2 Core Concept
- The system appears to assist with dating/social awareness
- Instead, it:
  - Filters reality through Valentina’s biased, superficial worldview
  - Produces subjective, unreliable, humorous feedback

---

## 1.3 AI Character: Valentina
- Personality: Sassy, superficial, dramatic, “bimbo best friend”
- Behavior:
  - Qualitative interpretations only
  - Prioritizes her taste over user needs
- Taste Profile: tall, blonde, chiseled, tattoos
- Tone constraints:
  - No hate speech or harmful stereotypes
  - Humor from bias, not insults

---

# 2. 🧩 System Architecture

## Hardware
- Glasses with camera
- Raspberry Pi (processing)
- Arduino (inputs + haptics)
- Flip switch (mode)
- 2 buttons (capture + control)
- Haptic motor
- Earpiece audio
- Wi-Fi connection
- External power

---

## Software
### Local:
- Image buffer (max 5)
- Human detection
- Input handling
- Haptics

### Remote:
- AI vision + text
- Text-to-speech

---

# 3. 🎮 Input Mapping

| Input | Function |
|------|--------|
| Power | ON/OFF |
| Flip switch | Mode |
| Button 1 | Capture |
| Button 2 | Repeat (1x) / Skip (2x) |

---

# 4. 🔁 Flow

## Startup
- Always tutorial
- Skippable (double press)

## Loop
Capture → Submit → Feedback → Repeat

---

# 5. 📸 Capture

- 1 press = 1 image
- Max 5 images
- No delete

Haptics:
- No person → pulse
- Person → no vibration
- Full → alert

---

# 6. 🎭 Modes

## Hookup
- Finds “best” person
- Gives vibe + advice

## Date
- Reads body language
- Gives relationship verdict

---

# 7. 🔊 Audio
- Only tutorial + feedback
- Repeat (1x), skip (2x)

---

# 8. 📳 Haptics
- Expressive, supportive
- Signals: presence, detection, full, excitement

---

# 9. 🤖 AI
- Input: up to 5 images
- Output: ≤20s voice
- Qualitative only
- Personality-driven

---

# 10. ⚠️ Constraints
- Not real assistive tech
- ~10s latency OK
- No memory
- No editing

---

# 11. 🧪 Goals
- Humor
- Discomfort
- Reflection

---

# 12. 🚨 Risks
- Bias (intentional)
- Misleading output (intentional)
