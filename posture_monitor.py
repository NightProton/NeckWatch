"""
╔══════════════════════════════════════════════════════════════════════╗
║  NECK-BEND POSTURE MONITOR  ·  v8.0  ·  Cyberpunk Live Dashboard     ║
║  Arduino UNO R4 Minima  +  Muscle BioAmp Shield v0.3                ║
╠══════════════════════════════════════════════════════════════════════╣
║  v8.0 — bigger, faster, more cyberpunk:                             ║
║  · Scroll-back window 10s → 25s and score history 300 → 900 pts,   ║
║    so both panels show a lot more context before scrolling off.    ║
║  · Redraw rate 25fps → 50fps (FPS_MS 40→20) for smoother motion.   ║
║  · Score panel now has a real time axis (seconds) like the EMG one.║
║  · New HUD chrome: animated CRT scanline sweep, corner brackets on ║
║    both panels, live session-timer readout, live PEAK score        ║
║    readout, and a subtle glitch-jitter on the alarm banner.        ║
║  · v7.0 behavior kept: live EMG + posture score + beep alarm only. ║
║  · Sensitivity: SCORE_ALARM=20, ALERT_HOLD_SEC=0.6s (unchanged).   ║
║  · Fixes "Access is denied" on the COM port: that error means       ║
║    something else already has it open (Arduino Serial Monitor, or  ║
║    a previous run of this script that didn't exit cleanly). The    ║
║    script now retries a few times with a clear message instead of  ║
║    dying instantly, and always closes the port on exit.            ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import sys, os, time, threading, io, wave, tempfile, traceback
from collections import deque

try:
    import numpy as np
    import serial
    import serial.tools.list_ports
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    import matplotlib.patheffects as pe
    import matplotlib.font_manager as fm
except ImportError as e:
    print(f"\n[MISSING LIBRARY]  {e}")
    print("Run:  pip install pyserial numpy matplotlib\n")
    sys.exit(1)

# ── Cyberpunk-style fonts (Orbitron for big display text, Rajdhani for HUD ──
# ── body text). Both are free/open-license Google Fonts, not the actual    ──
# ── in-game CD Projekt Red font (which isn't freely redistributable).      ──
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
DISPLAY_FONT = "monospace"   # big glowing headers / SCORE / alarm banner
BODY_FONT    = "monospace"   # axis labels, ticks, HUD readouts
try:
    for _fname in ("Orbitron.ttf", "Rajdhani-Regular.ttf", "Rajdhani-Bold.ttf", "Rajdhani-SemiBold.ttf"):
        _fpath = os.path.join(FONT_DIR, _fname)
        if os.path.isfile(_fpath):
            fm.fontManager.addfont(_fpath)
    DISPLAY_FONT = fm.FontProperties(fname=os.path.join(FONT_DIR, "Orbitron.ttf")).get_name()
    BODY_FONT = fm.FontProperties(fname=os.path.join(FONT_DIR, "Rajdhani-SemiBold.ttf")).get_name()
    print(f"[FONT]  Cyberpunk theme loaded: display='{DISPLAY_FONT}'  body='{BODY_FONT}'")
except Exception as e:
    print(f"[FONT]  Couldn't load custom fonts ({e}) — falling back to monospace. "
          f"Make sure the 'fonts' folder sits next to this script.")

# ── Sound backend: sounddevice → winsound → pygame → terminal beep ───
SND = "none"
try:
    import sounddevice as sd
    SND = "sounddevice"
    print("[SOUND]  Backend: sounddevice ✓")
except (ImportError, OSError):
    try:
        import winsound
        SND = "winsound"
        print("[SOUND]  Backend: winsound ✓")
    except ImportError:
        try:
            import pygame
            pygame.mixer.init(frequency=44100, size=-16, channels=1, buffer=512)
            SND = "pygame"
            print("[SOUND]  Backend: pygame ✓")
        except Exception:
            SND = "beep"
            print("[SOUND]  Backend: terminal beep (install sounddevice for real audio)")


# ══════════════════════════════════════════════════════════════════════
#  CONFIG — sensitivity raised vs v6.0
# ══════════════════════════════════════════════════════════════════════
PORT             = None     # None = auto-detect, or set e.g. "COM11"
BAUD             = 115_200

SCORE_ALARM      = 20       # was 28 — fires sooner now
ALERT_HOLD_SEC   = 0.6      # was 1.0 — less time bent before alarm fires
ALERT_COOLDOWN   = 6        # was 8 — alarms can repeat a bit sooner

RECONNECT_DELAY_SEC = 2.0
PORT_OPEN_RETRIES    = 5    # retries on "Access is denied" / busy port before giving up
PORT_OPEN_RETRY_WAIT = 1.5

DISPLAY_SECS  = 25         # was 10 — much longer EMG scroll-back window
SAMPLE_RATE   = 476        # matches Arduino SAMPLE_DELAY_MS=2 (~476Hz) — only change if the .ino changes too
DISPLAY_LEN   = DISPLAY_SECS * SAMPLE_RATE
RAW_DECIMATE  = 5          # was 4 — slightly higher so the longer window still draws fast
FPS_MS        = 20         # was 40 — 50fps redraw instead of 25fps, noticeably smoother
SCORE_HISTORY = 900        # was 300 — ~3x more posture-score history retained on screen


# ══════════════════════════════════════════════════════════════════════
#  PORT HANDLING
# ══════════════════════════════════════════════════════════════════════
def find_arduino_port():
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc, mfr, vid = (p.description or "").lower(), (p.manufacturer or "").lower(), p.vid
        if vid in (0x2341, 0x1A86, 0x0403):
            candidates.insert(0, p.device)
        elif any(k in desc or k in mfr for k in ("arduino", "ch340", "cp210", "ftdi", "uno")):
            candidates.append(p.device)
        elif p.device.startswith(("/dev/ttyACM", "/dev/ttyUSB", "COM")):
            candidates.append(p.device)
    return candidates[0] if candidates else None


def open_port(port, baud):
    """Open the serial port, retrying on 'busy/access denied' instead of
    dying instantly. That error means another program (Arduino IDE's
    Serial Monitor/Plotter, or a previous unclosed run of this script)
    already has the port open."""
    last_err = None
    for attempt in range(1, PORT_OPEN_RETRIES + 1):
        try:
            return serial.Serial(port, baud, timeout=1)
        except serial.SerialException as e:
            last_err = e
            msg = str(e).lower()
            if "access is denied" in msg or "permissionerror" in msg or "could not open port" in msg:
                print(f"[RETRY {attempt}/{PORT_OPEN_RETRIES}]  {port} is busy — "
                      f"close Arduino IDE's Serial Monitor/Plotter and any other "
                      f"running copy of this script. Retrying in {PORT_OPEN_RETRY_WAIT}s…")
            else:
                print(f"[RETRY {attempt}/{PORT_OPEN_RETRIES}]  {e}")
            time.sleep(PORT_OPEN_RETRY_WAIT)
    raise last_err


# ══════════════════════════════════════════════════════════════════════
#  CYBERPUNK PALETTE
# ══════════════════════════════════════════════════════════════════════
BG, PANEL, BORDER, GRID = "#030508", "#080D18", "#152A4A", "#0C1828"
NEON_CYAN, NEON_PINK, NEON_GREEN = "#00E5FF", "#FF1AAD", "#00FF88"
NEON_AMBER, NEON_RED = "#FFBE00", "#FF1744"
TEXT, DIM = "#D0F0FF", "#2A4A6A"

def glow(color, lw=7, alpha=0.28):
    return [pe.SimpleLineShadow(offset=(0, 0), shadow_color=color, alpha=alpha, linewidth=lw), pe.Normal()]


# ══════════════════════════════════════════════════════════════════════
#  ALARM SOUND — simple ascending two-tone beep
# ══════════════════════════════════════════════════════════════════════
SR = 44100

def _synth_beep(freq, duration_ms=220, vol=0.8):
    n = int(SR * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n, endpoint=False)
    sig = np.sin(2 * np.pi * freq * t)
    env = np.ones(n)
    atk, rel = int(n * 0.05), int(n * 0.3)
    env[:atk] = np.linspace(0, 1, atk)
    env[-rel:] = np.linspace(1, 0, rel)
    return (sig * env * vol).astype(np.float32)

_ALARM_FREQS = [880, 1100, 880, 1100]
_ALARM_DURS  = [180, 180, 180, 220]
_WAV_FILES, _PYG_SOUNDS = {}, {}

def _prepare_sounds():
    for f, d in zip(_ALARM_FREQS, _ALARM_DURS):
        if f in _WAV_FILES:
            continue
        arr = _synth_beep(f, d)
        if SND in ("winsound", "pygame"):
            pcm = (arr * 32767).astype(np.int16).tobytes()
            out = io.BytesIO()
            with wave.open(out, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(pcm)
            if SND == "winsound":
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp.write(out.getvalue()); tmp.close()
                _WAV_FILES[f] = tmp.name
            else:
                out.seek(0)
                _PYG_SOUNDS[f] = pygame.mixer.Sound(out)

_prepare_sounds()

def play_alarm():
    def _go():
        for freq, dur in zip(_ALARM_FREQS, _ALARM_DURS):
            try:
                if SND == "sounddevice":
                    sd.play(_synth_beep(freq, dur), SR); sd.wait(); time.sleep(0.02)
                elif SND == "winsound":
                    winsound.PlaySound(_WAV_FILES.get(freq), winsound.SND_FILENAME | winsound.SND_NOSTOP)
                    time.sleep(dur / 1000 + 0.03)
                elif SND == "pygame":
                    s = _PYG_SOUNDS.get(freq)
                    if s: s.play()
                    time.sleep(dur / 1000 + 0.03)
                else:
                    print("\a", end="", flush=True); time.sleep(0.25)
            except Exception as ex:
                print(f"[SND] {ex}")
    threading.Thread(target=_go, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════
#  SHARED STATE
# ══════════════════════════════════════════════════════════════════════
raw_buf   = deque([512] * DISPLAY_LEN, maxlen=DISPLAY_LEN)
score_buf = deque([0] * SCORE_HISTORY, maxlen=SCORE_HISTORY)
peak_score = 0

baseline_rms = None
thresh_long  = None

last_alert_t   = 0.0
alert_flash    = 0
session_alerts = 0
session_start  = time.time()
latest_score   = 0
latest_bend    = 0

status_text, status_col = "Waiting for Arduino…", DIM
calibrating = True

_bad_hold_start = None
ser, ser_lock = None, threading.Lock()
ser_queue = deque(maxlen=4000)
last_data_t = [time.time()]
_frame_error_logged = False


# ══════════════════════════════════════════════════════════════════════
#  SERIAL READER THREAD
# ══════════════════════════════════════════════════════════════════════
def serial_reader():
    global calibrating, baseline_rms, thresh_long, status_text, status_col, ser

    while True:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            if line == "CALIBRATING":
                status_text, status_col, calibrating = "CALIBRATING — sit STRAIGHT, muscles RELAXED…", NEON_AMBER, True
                continue
            if line.startswith("BASELINE:"):
                try:
                    baseline_rms = float(line.split(":")[1])
                    status_text, status_col, calibrating = (
                        f"CALIBRATED · Baseline={baseline_rms:.1f} · DETECTION ACTIVE", NEON_GREEN, False)
                except ValueError:
                    pass
                continue
            if line.startswith("THRESH_LONG:"):
                try: thresh_long = float(line.split(":")[1])
                except ValueError: pass
                continue
            if line.startswith("THRESH_SHORT:") or line.startswith("THRESH_TREND:"):
                continue  # not displayed in the trimmed UI

            with ser_lock:
                ser_queue.append(line)
                last_data_t[0] = time.time()

        except (serial.SerialException, OSError):
            status_text, status_col = "SERIAL DISCONNECTED — reconnecting…", NEON_RED
            try:
                if ser is not None:
                    ser.close()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY_SEC)
            try:
                target_port = PORT or find_arduino_port()
                if target_port:
                    ser = open_port(target_port, BAUD)
                    status_text, status_col, calibrating = f"RECONNECTED on {target_port} — recalibrating…", NEON_AMBER, True
                else:
                    status_text = "SERIAL DISCONNECTED — no Arduino port found, retrying…"
            except Exception:
                pass
        except Exception:
            time.sleep(0.05)


# ══════════════════════════════════════════════════════════════════════
#  ALERT LOGIC
# ══════════════════════════════════════════════════════════════════════
def fire_alert(score):
    global last_alert_t, alert_flash, session_alerts
    now = time.time()
    if now - last_alert_t < ALERT_COOLDOWN:
        return
    last_alert_t, session_alerts = now, session_alerts + 1
    alert_flash = int(3.0 * 1000 / FPS_MS)

    ts = time.strftime("%H:%M:%S")
    print(f"\n{'█'*56}\n  ⚠ NECK BEND ALERT [{ts}]  Score={score}\n  → Lift your gaze · roll shoulders back · sit tall\n{'█'*56}\n")
    play_alarm()


# ══════════════════════════════════════════════════════════════════════
#  BUILD FIGURE — two panels: raw EMG, posture score
# ══════════════════════════════════════════════════════════════════════
matplotlib.rcParams.update({
    "font.family": BODY_FONT, "text.color": TEXT, "axes.labelcolor": DIM,
    "xtick.color": DIM, "ytick.color": DIM, "axes.edgecolor": BORDER,
    "axes.facecolor": PANEL, "figure.facecolor": BG, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.4, "axes.spines.top": False,
    "axes.spines.right": False, "xtick.labelsize": 8, "ytick.labelsize": 8,
})

fig, (ax_raw, ax_score) = plt.subplots(2, 1, figsize=(11, 7), facecolor=BG,
                                        gridspec_kw={"height_ratios": [1.1, 1], "hspace": 0.55})
fig.canvas.manager.set_window_title("POSTURE MONITOR v8.0")
plt.subplots_adjust(left=0.08, right=0.96, top=0.86, bottom=0.08)

for ax in (ax_raw, ax_score):
    for sp in ax.spines.values():
        sp.set_color(BORDER); sp.set_linewidth(0.8)

fig.text(0.5, 0.965, "// NEURAL POSTURE MONITOR // LIVE EMG //", ha="center", va="top",
          fontsize=15, fontweight="bold", color=NEON_CYAN, fontfamily=DISPLAY_FONT,
          path_effects=glow(NEON_CYAN, 9, 0.32))
status_obj = fig.text(0.5, 0.92, status_text, ha="center", va="top", fontsize=9.5,
                       color=status_col, fontfamily=BODY_FONT, fontweight="bold")

# ── Raw EMG panel ──
ax_raw.set_title("RAW EMG · UPPER TRAPEZIUS  (forward neck -> ^ activity)",
                  color=NEON_CYAN, fontsize=10, fontweight="bold", loc="left", pad=4,
                  fontfamily=BODY_FONT)
ax_raw.set_ylabel("ADC", fontsize=8, fontfamily=BODY_FONT)
ax_raw.set_ylim(0, 1023)
ax_raw.set_xlim(0, DISPLAY_LEN)
ax_raw.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x/SAMPLE_RATE:.0f}s"))
x_raw_arr = np.arange(DISPLAY_LEN)
x_raw_dec = x_raw_arr[::RAW_DECIMATE]
line_raw, = ax_raw.plot([], [], lw=0.9, color=NEON_CYAN, alpha=0.95, path_effects=glow(NEON_CYAN, 5, 0.22))
thresh_l_line = ax_raw.axhline(0, color=NEON_RED, lw=0.8, ls="--", alpha=0)
live_score_txt = ax_raw.text(0.99, 0.92, "SCORE: —", transform=ax_raw.transAxes, ha="right", va="top",
                              fontsize=13, fontweight="bold", color=NEON_GREEN, fontfamily=DISPLAY_FONT)
live_bend_txt = ax_raw.text(0.99, 0.72, "FLAG: —", transform=ax_raw.transAxes, ha="right", va="top",
                             fontsize=10, color=DIM, fontfamily=BODY_FONT, fontweight="bold")

# ── Score panel ──
ax_score.set_title(f"POSTURE SCORE · alert ≥ {SCORE_ALARM} held {ALERT_HOLD_SEC}s",
                    color=NEON_PINK, fontsize=10, fontweight="bold", loc="left", pad=4,
                    fontfamily=BODY_FONT)
ax_score.set_ylabel("Score", fontsize=8, fontfamily=BODY_FONT)
ax_score.set_ylim(0, 105)
ax_score.set_xlim(0, SCORE_HISTORY)
ax_score.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x/SAMPLE_RATE:.0f}s"))
ax_score.axhspan(0, SCORE_ALARM, alpha=0.08, color=NEON_GREEN)
ax_score.axhspan(SCORE_ALARM, 65, alpha=0.08, color=NEON_AMBER)
ax_score.axhspan(65, 105, alpha=0.08, color=NEON_RED)
ax_score.axhline(SCORE_ALARM, color=NEON_AMBER, lw=0.9, ls="--", alpha=0.75)
line_score, = ax_score.plot([], [], lw=2.0, color=NEON_PINK, path_effects=glow(NEON_PINK))
hold_txt = ax_score.text(0.99, 0.92, "", transform=ax_score.transAxes, ha="right", va="top",
                          fontsize=10, color=NEON_AMBER, fontfamily=BODY_FONT, fontweight="bold")
alerts_txt = ax_score.text(0.01, 0.92, "ALERTS: 0", transform=ax_score.transAxes, ha="left", va="top",
                            fontsize=9, color=DIM, fontfamily=BODY_FONT, fontweight="bold")
peak_score_txt = ax_score.text(0.01, 0.06, "PEAK: 0", transform=ax_score.transAxes, ha="left", va="bottom",
                                fontsize=9, color=DIM, fontfamily=BODY_FONT, fontweight="bold")

# ── Full-screen alarm flash overlay ── (disabled: banner text removed)
alarm_scrim = fig.add_axes([0, 0, 1, 1], zorder=10)
alarm_scrim.axis("off")
alarm_scrim.set_facecolor(NEON_RED); alarm_scrim.patch.set_alpha(0)
alarm_title = fig.text(0.5, 0.5, "", ha="center", va="center",
                        fontsize=30, fontweight="bold", color=NEON_RED, alpha=0,
                        fontfamily=DISPLAY_FONT, zorder=11, path_effects=glow(NEON_RED, 12, 0.5))
alarm_sub = fig.text(0.5, 0.43, "",
                      ha="center", va="center", fontsize=13, color=TEXT, alpha=0,
                      fontfamily=BODY_FONT, fontweight="bold", zorder=11)


# ── HUD corner brackets — static cyberpunk chrome on each panel ──
def _corner_brackets(ax, size=0.045, color=NEON_CYAN, lw=1.3, alpha=0.55):
    for (cx, cy, dx, dy) in [(0, 0, 1, 1), (1, 0, -1, 1), (0, 1, 1, -1), (1, 1, -1, -1)]:
        ax.plot([cx, cx + dx * size], [cy, cy], transform=ax.transAxes,
                 color=color, lw=lw, alpha=alpha, clip_on=False, solid_capstyle="round")
        ax.plot([cx, cx], [cy, cy + dy * size], transform=ax.transAxes,
                 color=color, lw=lw, alpha=alpha, clip_on=False, solid_capstyle="round")

_corner_brackets(ax_raw, color=NEON_CYAN)
_corner_brackets(ax_score, color=NEON_PINK)

# ── Animated CRT scanline sweep — a faint band drifting down the whole window ──
scan_ax = fig.add_axes([0, 0, 1, 1], zorder=9)
scan_ax.axis("off")
scan_ax.set_facecolor("none")
scan_rect = plt.Rectangle((0, 1.0), 1, 0.006, transform=scan_ax.transAxes,
                           color=NEON_CYAN, alpha=0.10, lw=0)
scan_ax.add_patch(scan_rect)
_scan_y = [1.0]

# ── Session timer readout ──
session_timer_txt = fig.text(0.965, 0.965, "T+00:00:00", ha="right", va="top", fontsize=10,
                              color=NEON_CYAN, fontfamily=DISPLAY_FONT, alpha=0.85)

_ANIMATED_ARTISTS = [line_raw, line_score, thresh_l_line, live_score_txt, live_bend_txt,
                     hold_txt, alerts_txt, peak_score_txt, status_obj, alarm_scrim.patch,
                     alarm_title, alarm_sub, scan_rect, session_timer_txt]
_ALARM_ARTISTS = [alarm_title, alarm_sub]


# ══════════════════════════════════════════════════════════════════════
#  FRAME UPDATE
# ══════════════════════════════════════════════════════════════════════
def _update_impl(frame):
    global alert_flash, _bad_hold_start, latest_score, latest_bend, peak_score

    status_obj.set_text(status_text)
    status_obj.set_color(status_col)

    with ser_lock:
        lines = list(ser_queue)
        ser_queue.clear()

    for line in lines[-200:]:
        parts = line.split(",")
        if len(parts) != 6:
            continue
        try:
            raw, rmsS, rmsL, rmsT, score, bend = (int(float(p)) for p in parts)
        except ValueError:
            continue
        raw_buf.append(raw)
        score_buf.append(score)
        latest_score, latest_bend = score, bend

    # raw EMG trace
    arr = np.array(raw_buf)[::RAW_DECIMATE]
    line_raw.set_data(x_raw_dec[:len(arr)], arr)

    if thresh_long is not None:
        thresh_l_line.set_ydata([thresh_long, thresh_long])
        thresh_l_line.set_alpha(0.5)

    score_color = NEON_RED if latest_score >= 65 else NEON_AMBER if latest_score >= SCORE_ALARM else NEON_GREEN
    live_score_txt.set_text(f"SCORE: {latest_score:3d}")
    live_score_txt.set_color(score_color)
    bend_label, bend_color = ("[!] BEND", NEON_RED) if latest_bend else ("[OK]", NEON_GREEN)
    live_bend_txt.set_text(f"FLAG:  {bend_label}")
    live_bend_txt.set_color(bend_color)

    sc = list(score_buf)
    line_score.set_data(np.arange(len(sc)), sc)
    line_score.set_color(score_color)

    if latest_score > peak_score:
        peak_score = latest_score
    peak_score_txt.set_text(f"PEAK: {peak_score:3d}")
    peak_score_txt.set_color(NEON_RED if peak_score >= 65 else NEON_AMBER if peak_score >= SCORE_ALARM else DIM)

    # ── hold timer → fires alert ──
    now = time.time()
    if not calibrating and latest_score >= SCORE_ALARM:
        if _bad_hold_start is None:
            _bad_hold_start = now
        held = now - _bad_hold_start
        hold_txt.set_text(f"Bent {held:.1f}s / {ALERT_HOLD_SEC}s")
        hold_txt.set_color(NEON_RED if held > ALERT_HOLD_SEC * 0.6 else NEON_AMBER)
        if held >= ALERT_HOLD_SEC:
            fire_alert(latest_score)
            _bad_hold_start = now  # avoid re-firing every frame; cooldown also gates this
    else:
        _bad_hold_start = None
        hold_txt.set_text("")

    alerts_txt.set_text(f"ALERTS: {session_alerts}")

    # ── session timer ──
    elapsed = int(now - session_start)
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)
    session_timer_txt.set_text(f"T+{h:02d}:{m:02d}:{s:02d}")

    # ── CRT scanline sweep — cheap continuous drift, wraps top→bottom ──
    _scan_y[0] -= 0.014
    if _scan_y[0] < -0.02:
        _scan_y[0] = 1.0
    scan_rect.set_y(_scan_y[0])

    # ── alarm flash overlay ── (banner disabled; keep counters/decay logic intact)
    if alert_flash > 0:
        alert_flash -= 1
    alarm_scrim.patch.set_alpha(0)
    for t in _ALARM_ARTISTS:
        t.set_alpha(0)

    return _ANIMATED_ARTISTS


def update(frame):
    global _frame_error_logged
    try:
        return _update_impl(frame)
    except Exception:
        if not _frame_error_logged:
            print("\n[UPDATE ERROR] Frame error — recovering.\n")
            traceback.print_exc()
            _frame_error_logged = True
        return _ANIMATED_ARTISTS


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":

    if PORT is None:
        detected = find_arduino_port()
        if detected:
            PORT = detected
            print(f"[AUTO-DETECT]  Found Arduino on {PORT}")
        else:
            print("\n[ERROR]  No Arduino port found automatically.")
            print("  → Plug in the Arduino, then re-run, or set PORT = 'COMx' at the top.\n")
            sys.exit(1)

    print(f"""
╔══════════════════════════════════════════════════════╗
║   NECK-BEND POSTURE MONITOR  v8.0  ·  starting…     ║
╠══════════════════════════════════════════════════════╣
║  Port      : {PORT:<40}║
║  Baud      : {BAUD:<40}║
║  Threshold : score ≥ {SCORE_ALARM} held {ALERT_HOLD_SEC}s{'':<22}║
║  Cooldown  : {ALERT_COOLDOWN}s between alerts{'':<25}║
║  Window    : {DISPLAY_SECS}s EMG / {SCORE_HISTORY}pt score history{'':<12}║
║  Refresh   : {1000/FPS_MS:.0f}fps ({FPS_MS}ms/frame){'':<24}║
╚══════════════════════════════════════════════════════╝
""")

    try:
        ser = open_port(PORT, BAUD)
        print(f"[OK]  Connected to Arduino on {PORT}")
    except serial.SerialException as e:
        print(f"\n[ERROR]  Cannot open {PORT} after {PORT_OPEN_RETRIES} attempts:\n  {e}")
        print("  → This usually means another program already has the port open:")
        print("      - Close Arduino IDE's Serial Monitor / Serial Plotter")
        print("      - Make sure no earlier copy of this script is still running")
        print("      - Unplug/replug the Arduino, or try a different USB port")
        sys.exit(1)

    threading.Thread(target=serial_reader, daemon=True).start()

    # blit=False: several HUD artists (fig.text() labels, the alarm overlay patch)
    # aren't attached to any single Axes, so matplotlib's blit path crashes trying
    # to look up ax._get_view() for them. Full redraw avoids that entirely.
    ani = animation.FuncAnimation(fig, update, interval=FPS_MS, blit=False, cache_frame_data=False)

    print("""
HOW TO USE:
  1. Upload posture_monitor.ino to the Arduino first.
  2. Electrodes on UPPER TRAPEZIUS: IN+/IN- 2cm apart on the muscle,
     REF on a bony point (elbow/wrist).
  3. Sit UPRIGHT, relaxed, for 4s while it calibrates (LED blinks).
  4. LED solid = monitoring. Bend your neck forward → alarm after
     it's held past the threshold.

SENSITIVITY (this file, top of CONFIG section):
  SCORE_ALARM     lower = more sensitive   (now 20, was 28)
  ALERT_HOLD_SEC  shorter = fires faster   (now 0.6s, was 1.0s)
  ALERT_COOLDOWN  longer = fewer repeats   (now 6s, was 8s)
""")

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        if ser and ser.is_open:
            ser.close()
        for p in _WAV_FILES.values():
            try: os.unlink(p)
            except Exception: pass
        print("\n── Session ended ──")
        print(f"   Total alerts : {session_alerts}")