// ── Valentina Arduino Sketch ──────────────────────────────────────────────────
// Reads: Button 1 (short/long), Button 2 (short/long), Flip switch, Power switch
// Writes: Haptic motor patterns
// Communicates with Raspberry Pi via USB Serial at 9600 baud

// ── Pin assignments ───────────────────────────────────────────────────────────
const int BTN1_PIN   = 2;
const int BTN2_PIN   = 3;
const int FLIP_PIN   = 4;
const int POWER_PIN  = 5;
const int HAPTIC_PIN = 9;   // PWM-capable pin, motor via transistor/driver

// ── Timing constants ──────────────────────────────────────────────────────────
const unsigned long LONG_PRESS_MS    = 1000; // hold duration for long press
const unsigned long SWITCH_SETTLE_MS = 300;  // switch must be stable this long before registering

// ── Button 1 state ────────────────────────────────────────────────────────────
bool          btn1_prev        = false;
bool          btn1_held        = false;
bool          btn1_long_fired  = false;
unsigned long btn1_press_start = 0;

// ── Button 2 state ────────────────────────────────────────────────────────────
bool          btn2_prev        = false;
bool          btn2_held        = false;
bool          btn2_long_fired  = false;
unsigned long btn2_press_start = 0;

// ── Flip switch: settle-based debounce ────────────────────────────────────────
int           last_flip        = -1;
int           flip_reading     = HIGH;
unsigned long flip_last_change = 0;

// ── Power switch: settle-based debounce ───────────────────────────────────────
int           last_power        = -1;
int           power_reading     = HIGH;
unsigned long power_last_change = 0;


// ── Haptic patterns ───────────────────────────────────────────────────────────

// Two short buzzes — verdict is ready (notification style)
void hap_notify() {
  digitalWrite(HAPTIC_PIN, HIGH); delay(80);
  digitalWrite(HAPTIC_PIN, LOW);  delay(100);
  digitalWrite(HAPTIC_PIN, HIGH); delay(80);
  digitalWrite(HAPTIC_PIN, LOW);
}

// Single quick pulse ~50ms — HOT (Python repeats rapidly: every 0.15s)
void hap_hot() {
  digitalWrite(HAPTIC_PIN, HIGH); delay(50);
  digitalWrite(HAPTIC_PIN, LOW);
}

// Single medium pulse ~200ms — MEH (Python fires slowly: every 1.5s)
void hap_meh() {
  digitalWrite(HAPTIC_PIN, HIGH); delay(200);
  digitalWrite(HAPTIC_PIN, LOW);
}

// Single strong burst ~150ms — YUCKY (Python repeats urgently: every 0.3s)
void hap_yucky() {
  digitalWrite(HAPTIC_PIN, HIGH); delay(150);
  digitalWrite(HAPTIC_PIN, LOW);
}

// Three firm pulses — buffer full or alert
void hap_alert() {
  for (int i = 0; i < 3; i++) {
    digitalWrite(HAPTIC_PIN, HIGH); delay(150);
    digitalWrite(HAPTIC_PIN, LOW);  delay(100);
  }
}

// Two short bursts — no person detected
void hap_pulse() {
  digitalWrite(HAPTIC_PIN, HIGH); delay(80);
  digitalWrite(HAPTIC_PIN, LOW);  delay(80);
  digitalWrite(HAPTIC_PIN, HIGH); delay(80);
  digitalWrite(HAPTIC_PIN, LOW);
}


// ── Setup ─────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(9600);
  pinMode(BTN1_PIN,   INPUT_PULLUP);
  pinMode(BTN2_PIN,   INPUT_PULLUP);
  pinMode(FLIP_PIN,   INPUT_PULLUP);
  pinMode(POWER_PIN,  INPUT_PULLUP);
  pinMode(HAPTIC_PIN, OUTPUT);
  digitalWrite(HAPTIC_PIN, LOW);

  delay(100);

  // Send initial switch states so Pi knows configuration on boot
  flip_reading  = digitalRead(FLIP_PIN);
  last_flip     = flip_reading;
  Serial.println(last_flip == LOW ? "FLIP_HOOKUP" : "FLIP_DATE");

  power_reading = digitalRead(POWER_PIN);
  last_power    = power_reading;
  Serial.println(last_power == LOW ? "POWER_ON" : "POWER_OFF");
}


// ── Main loop ─────────────────────────────────────────────────────────────────

void loop() {
  unsigned long now = millis();

  // ── Button 1: short / long press ─────────────────────────────────────────
  bool btn1 = (digitalRead(BTN1_PIN) == LOW);

  if (btn1 && !btn1_prev) {
    btn1_held        = true;
    btn1_long_fired  = false;
    btn1_press_start = now;
  }
  if (btn1_held && !btn1_long_fired && (now - btn1_press_start >= LONG_PRESS_MS)) {
    Serial.println("BTN1_LONG");
    btn1_long_fired = true;
  }
  if (!btn1 && btn1_prev) {
    if (!btn1_long_fired) Serial.println("BTN1_SHORT");
    btn1_held = false;
  }
  btn1_prev = btn1;

  // ── Button 2: short / long press ─────────────────────────────────────────
  bool btn2 = (digitalRead(BTN2_PIN) == LOW);

  if (btn2 && !btn2_prev) {
    btn2_held        = true;
    btn2_long_fired  = false;
    btn2_press_start = now;
  }
  if (btn2_held && !btn2_long_fired && (now - btn2_press_start >= LONG_PRESS_MS)) {
    Serial.println("BTN2_LONG");
    btn2_long_fired = true;
  }
  if (!btn2 && btn2_prev) {
    if (!btn2_long_fired) Serial.println("BTN2_SHORT");
    btn2_held = false;
  }
  btn2_prev = btn2;

  // ── Flip switch: settle-based debounce ───────────────────────────────────
  // Only registers after the switch has held its new state for SWITCH_SETTLE_MS.
  // Eliminates all mechanical bounce regardless of switch quality.
  int raw_flip = digitalRead(FLIP_PIN);
  if (raw_flip != flip_reading) {
    flip_reading     = raw_flip;
    flip_last_change = now;
  }
  if ((now - flip_last_change >= SWITCH_SETTLE_MS) && (flip_reading != last_flip)) {
    last_flip = flip_reading;
    Serial.println(last_flip == LOW ? "FLIP_HOOKUP" : "FLIP_DATE");
  }

  // ── Power switch: settle-based debounce ──────────────────────────────────
  int raw_power = digitalRead(POWER_PIN);
  if (raw_power != power_reading) {
    power_reading     = raw_power;
    power_last_change = now;
  }
  if ((now - power_last_change >= SWITCH_SETTLE_MS) && (power_reading != last_power)) {
    last_power = power_reading;
    Serial.println(last_power == LOW ? "POWER_ON" : "POWER_OFF");
  }

  // ── Incoming commands from Pi ─────────────────────────────────────────────
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if      (cmd == "HAP_NOTIFY") hap_notify();
    else if (cmd == "HAP_HOT")    hap_hot();
    else if (cmd == "HAP_MEH")    hap_meh();
    else if (cmd == "HAP_YUCKY")  hap_yucky();
    else if (cmd == "HAP_ALERT")  hap_alert();
    else if (cmd == "HAP_PULSE")  hap_pulse();
  }

  delay(10);
}
