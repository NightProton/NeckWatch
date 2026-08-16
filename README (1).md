# NeckWatch

A wearable posture monitor built on the Arduino Uno R4 + Muscle BioAmp Shield. It detects
slouching in real time by reading surface EMG (electromyography) signals from the upper
trapezius muscle, tracks how long you've been holding a sustained forward-neck posture,
and triggers an alert (sound + on-screen dashboard) before it turns into a habit.

Built as a hands-on EMG biofeedback demo — hardware, firmware, and software all included
so it can be rebuilt and run again for future events.

---

## How it works

Forward head posture moves the head's weight (~5 kg) further from the spine's natural
support line, forcing the upper trapezius to fire continuously just to stop the head
from falling further forward. This isn't a shoulder shrug — it's the trapezius acting as
a neck extensor, straining isometrically against gravity. Unlike normal muscle use, which
cycles between contraction and rest, this is a **sustained, low-level contraction**, a
well-documented cause of neck/shoulder fatigue in desk-based work.

The device measures this directly:

1. Electrodes on the trapezius pick up the muscle's electrical activity (EMG).
2. The Arduino computes three rolling RMS ("signal strength") windows — short, long, and
   trend — and blends them into a 0–100 posture score.
3. If that score stays above threshold for a set hold time, an alert fires — a debounce
   gate filters out noise from talking, swallowing, or shifting in your seat.
4. The laptop-side Python script displays a live dashboard and plays the alert sound.

This is the same underlying principle used in clinical EMG biofeedback for posture
correction, just automated and built for everyday desk / event use.

---

## Hardware

| Part | Notes |
|---|---|
| Arduino Uno R4 (Minima or WiFi) | Main controller |
| Muscle BioAmp Shield v0.3 | EMG amplifier, stacks directly on the Arduino |
| 3x EMG electrodes | IN+ / IN- on the upper trapezius (~2 cm apart), REF on a bony point (elbow/wrist) |
| USB-C cable | Arduino ↔ laptop |
| Laptop | Runs the Python dashboard |

---

## Repo contents

```
posture_monitor.ino     Arduino firmware (v6.0) — sampling, RMS calc, calibration, alert logic
posture_monitor.py      Python dashboard (v8.0) — live graphs, scoring, sound alerts
Posture_Monitor_Guide   Full step-by-step build guide (hardware setup → calibration → tuning)
```

---

## Quick start

**1. Flash the Arduino**
Open `posture_monitor.ino` in the Arduino IDE, select **Arduino Uno R4** as the board,
select the correct port, and upload.

**2. Install Python dependencies**
```bash
pip install pyserial numpy matplotlib sounddevice
```

**3. Run the dashboard**
```bash
python posture_monitor.py
```
The script auto-detects the Arduino's port. On first connection, sit up straight and stay
relaxed for the 4-second on-device calibration (LED blinks fast → goes solid when done).

---

## Tuning sensitivity

All the sensitivity settings live in the `CONFIG` section near the top of
`posture_monitor.py`:

| Setting | Default | What it does |
|---|---|---|
| `SCORE_ALARM` | 20 | Lower = more sensitive. Raise if small movements (talking, swallowing) trigger false alerts. |
| `ALERT_HOLD_SEC` | 0.6 | How many seconds the score must stay above `SCORE_ALARM` before it counts as a real bend. |
| `ALERT_COOLDOWN` | 6 | Minimum seconds between repeated alerts. |
| `DISPLAY_SECS` | 25 | Seconds of EMG history shown on the top graph before scrolling. |

Change a value, save, and re-run — no re-flashing the Arduino needed.

---

## Full build guide

See `Posture_Monitor_Guide` for the complete walkthrough: electrode placement, skin prep,
Arduino IDE setup, Python environment setup, calibration, troubleshooting, and a quick
reference card you can print and keep at the workspace.

---

## Troubleshooting (short version)

| Problem | Fix |
|---|---|
| Flat/no signal | Re-press electrodes, check cable, try a new electrode |
| Noisy signal | Move the REF electrode to a bonier area (elbow/wrist) |
| "Access is denied" opening the port | Close Arduino IDE's Serial Monitor / any other running copy of the script |
| Alerts fire on small movements | Raise `SCORE_ALARM` or `ALERT_HOLD_SEC` |
| Alerts never fire | Lower `SCORE_ALARM`, check electrode contact, re-power the Arduino to recalibrate |

Full troubleshooting table is in the build guide.
