// ════════════════════════════════════════════════════════════════
//  NECK-BEND POSTURE MONITOR  —  Arduino UNO R4 Minima  (v6.0)
//  Muscle BioAmp Shield v0.3  on  A0
//  Electrodes: IN+/IN- on UPPER TRAPEZIUS (2 cm apart, either side)
//              REF on bony point (back of elbow / wrist bone)
//
//  v6.0 CHANGES vs v5.0:
//  ─────────────────────────────────────────────────────────────
//  • BALANCED sensitivity: SENS_LONG_X10 11→14, SENS_TREND_X10 10→13,
//    SCORE_THRESHOLD 20→28. v5.0 fired on swallows/talking/shifting;
//    this catches real sustained forward-neck tension while ignoring
//    momentary twitches.
//  • NEW: debounced alert gate (DEBOUNCE_SAMPLES=40, ~84ms). The old
//    code re-evaluated longAbove/trendAbove every single 2ms sample
//    with no hysteresis, so noise near the threshold line flickered
//    neckBendFlag 0/1/0/1 rapidly — which on the host side kept
//    resetting the ALERT_HOLD_SEC timer and made alarms unreliable.
//    Now the flag only flips after 40 consecutive samples agree.
//  • Fixed rounding in printed THRESH_* calibration values (cosmetic
//    only — did not affect detection math, which already used the
//    full-precision baselineX10).
//
//  HOW NECK-BEND IS DETECTED:
//  ─────────────────────────────────────────────────────────────
//  Forward neck bend loads the upper trapezius to hold the ~5 kg head.
//  Three RMS windows measure this:
//    1. SHORT-RMS  (100 ms) — fast spikes — score weight only
//    2. LONG-RMS   (300 ms) — sustained tension — primary gate
//    3. TREND-RMS  (1.2 s)  — progressive slouch — secondary gate
//
//  ALERT fires when:
//    postureScore >= SCORE_THRESHOLD  (28)
//    AND ( longRMS > 1.4× baseline   OR  trendRMS > 1.3× baseline )
//    AND that condition holds for 40 consecutive samples (~84 ms)
//
//  CALIBRATION (4 seconds on power-up):
//    → Keep neck STRAIGHT and muscles FULLY RELAXED
//    → LED blinks fast during calibration
//    → LED solid ON = calibration done, monitoring started
//
//  Serial CSV at ~476 Hz:
//    raw, rmsShort, rmsLong, rmsTrend, postureScore, neckBendFlag
// ════════════════════════════════════════════════════════════════

#define EMG_PIN       A0
#define LED_PIN       13

#define SAMPLE_DELAY_MS   2        // ~476 Hz

// Window sizes in samples
#define WIN_SHORT    50            // 100 ms
#define WIN_LONG     150           // 300 ms
#define WIN_TREND    600           // 1200 ms

#define CALIB_SECS   4
#define FLOOR_RMS    5

// ── Sensitivity multipliers (×10 fixed-point) ────────────────────
// BALANCED tuning: catches real sustained forward-neck bends,
// rides through small twitches (swallowing, talking, shifting).
#define SENS_SHORT_X10   16        // 1.6× baseline — score weight only
#define SENS_LONG_X10    14        // 1.4× baseline — alert gate
#define SENS_TREND_X10   13        // 1.3× baseline — slow slouch gate

// Score threshold — lower = more sensitive
#define SCORE_THRESHOLD  28

// ── Alert debounce ────────────────────────────────────────────────
// Requires N consecutive over-threshold samples before flag goes HIGH,
// and N consecutive under-threshold samples before it drops back to LOW.
// At ~476 Hz, 40 samples ≈ 84 ms — kills single-sample noise flicker
// without adding noticeable lag to genuine bends.
#define DEBOUNCE_SAMPLES 40

// ── Circular buffers ─────────────────────────────────────────────
int  shortBuf[WIN_SHORT];
int  longBuf [WIN_LONG ];
int  trendBuf[WIN_TREND];
int  shortIdx = 0, longIdx = 0, trendIdx = 0;
bool shortFull = false, longFull = false, trendFull = false;

long baselineX10 = FLOOR_RMS * 10L;
bool calibDone   = false;
int  debounceCounter = 0;   // + when over threshold, - when under
bool alertActive     = false;


// ── Integer square root ──────────────────────────────────────────
unsigned long isqrt(unsigned long n) {
  if (n == 0) return 0;
  unsigned long x = n, y = (x + 1) >> 1;
  while (y < x) { x = y; y = (x + n / x) >> 1; }
  return x;
}

// ── Compute RMS centred on ADC midpoint 512 ──────────────────────
unsigned long computeRMS(int* buf, int len) {
  unsigned long sumSq = 0;
  for (int i = 0; i < len; i++) {
    long v = (long)buf[i] - 512L;
    sumSq += (unsigned long)(v * v);
  }
  return isqrt(sumSq / (unsigned long)len);
}


void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { ; }

  pinMode(LED_PIN, OUTPUT);

  // Pre-fill with midpoint so RMS starts at zero
  for (int i = 0; i < WIN_SHORT; i++) shortBuf[i] = 512;
  for (int i = 0; i < WIN_LONG;  i++) longBuf[i]  = 512;
  for (int i = 0; i < WIN_TREND; i++) trendBuf[i] = 512;

  Serial.println("CALIBRATING");

  // Calibration: measure resting RMS — keep neck straight, muscles relaxed
  unsigned long sumSq = 0, count = 0;
  unsigned long calEnd = millis() + (unsigned long)CALIB_SECS * 1000UL;

  while (millis() < calEnd) {
    int v = analogRead(EMG_PIN) - 512;
    sumSq += (unsigned long)((long)v * v);
    count++;
    digitalWrite(LED_PIN, (millis() / 200) & 1);
    delay(SAMPLE_DELAY_MS);
  }

  unsigned long meanSq = (count > 0) ? (sumSq / count) : 1UL;
  unsigned long bRMS   = isqrt(meanSq);
  if (bRMS < (unsigned long)FLOOR_RMS) bRMS = FLOOR_RMS;

  baselineX10 = (long)bRMS * 10L;

  digitalWrite(LED_PIN, HIGH);
  calibDone = true;

  Serial.print("BASELINE:");      Serial.println(bRMS);
  Serial.print("THRESH_SHORT:");  Serial.println((bRMS * SENS_SHORT_X10 + 5) / 10);
  Serial.print("THRESH_LONG:");   Serial.println((bRMS * SENS_LONG_X10  + 5) / 10);
  Serial.print("THRESH_TREND:");  Serial.println((bRMS * SENS_TREND_X10 + 5) / 10);
}


void loop() {
  int raw = analogRead(EMG_PIN);

  shortBuf[shortIdx++] = raw;
  if (shortIdx >= WIN_SHORT) { shortIdx = 0; shortFull = true; }

  longBuf[longIdx++] = raw;
  if (longIdx >= WIN_LONG)  { longIdx  = 0; longFull  = true; }

  trendBuf[trendIdx++] = raw;
  if (trendIdx >= WIN_TREND) { trendIdx = 0; trendFull = true; }

  unsigned long rmsS = shortFull ? computeRMS(shortBuf, WIN_SHORT) : 0UL;
  unsigned long rmsL = longFull  ? computeRMS(longBuf,  WIN_LONG)  : 0UL;
  unsigned long rmsT = trendFull ? computeRMS(trendBuf, WIN_TREND) : 0UL;

  int neckBendFlag = 0;
  int postureScore = 0;

  if (calibDone && shortFull && longFull) {

    long scoreS = ((long)rmsS * 100L * 10L) / baselineX10 - 100L;
    long scoreL = ((long)rmsL * 100L * 10L) / baselineX10 - 100L;
    long scoreT = trendFull ? (((long)rmsT * 100L * 10L) / baselineX10 - 100L) : 0L;

    if (scoreS < 0) scoreS = 0;
    if (scoreL < 0) scoreL = 0;
    if (scoreT < 0) scoreT = 0;

    // Weighted blend: LONG dominates (sustained tension)
    postureScore = (int)((scoreS * 20L + scoreL * 65L + scoreT * 15L) / 100L);
    if (postureScore > 100) postureScore = 100;

    // Alert gates: 1.4× baseline for LONG, 1.3× for TREND
    bool longAbove  = ((long)rmsL * 100L > baselineX10 * (long)SENS_LONG_X10);
    bool trendAbove = trendFull && ((long)rmsT * 100L > baselineX10 * (long)SENS_TREND_X10);
    bool rawOver    = (postureScore >= SCORE_THRESHOLD) && (longAbove || trendAbove);

    // Debounce: require DEBOUNCE_SAMPLES consecutive samples in the new
    // state before actually flipping the flag. Stops single-sample noise
    // from toggling the alert and resetting the host-side hold timer.
    if (rawOver) {
      debounceCounter = (debounceCounter < 0) ? 1 : debounceCounter + 1;
    } else {
      debounceCounter = (debounceCounter > 0) ? -1 : debounceCounter - 1;
    }
    // Clamp so it can't climb indefinitely during a long sustained bend
    // or a long good-posture stretch — only the threshold crossing matters.
    if (debounceCounter > DEBOUNCE_SAMPLES)  debounceCounter = DEBOUNCE_SAMPLES;
    if (debounceCounter < -DEBOUNCE_SAMPLES) debounceCounter = -DEBOUNCE_SAMPLES;

    if (debounceCounter >= DEBOUNCE_SAMPLES)  alertActive = true;
    if (debounceCounter <= -DEBOUNCE_SAMPLES) alertActive = false;

    if (alertActive) {
      neckBendFlag = 1;
      digitalWrite(LED_PIN, (millis() / 100) & 1);  // fast blink = alert
    } else {
      digitalWrite(LED_PIN, HIGH);  // solid = good posture
    }
  }

  // CSV: raw, rmsShort, rmsLong, rmsTrend, postureScore, neckBendFlag
  Serial.print(raw);          Serial.print(',');
  Serial.print(rmsS);         Serial.print(',');
  Serial.print(rmsL);         Serial.print(',');
  Serial.print(rmsT);         Serial.print(',');
  Serial.print(postureScore); Serial.print(',');
  Serial.println(neckBendFlag);

  delay(SAMPLE_DELAY_MS);
}
