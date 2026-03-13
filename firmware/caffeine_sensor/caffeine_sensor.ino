/*
 * caffeine_sensor.ino
 *
 * Hardware:
 *   - SparkFun ICM-42688-P (lib v1.0.8) on I2C 0x68
 *   - SparkFun MAX30102 / MAX3010x (lib v1.1.2) on I2C 0x57
 *   - Both on Wire (I2C), 400 kHz
 *
 * Output (USB serial, 115200 baud):
 *   ASCII CSV, one line per sample at 100 Hz
 *   Header:  seq,ts_ms,ax,ay,az,gx,gy,gz,ppg
 *
 *   seq     : uint16 wrapping counter 0-65535 (2 bytes conceptually)
 *   ts_ms   : uint32 millis() since boot
 *   ax,ay,az: float m/s² (IMU ±4 g range)
 *   gx,gy,gz: float deg/s (IMU ±500 dps range)
 *   ppg     : uint32 raw ADC value from MAX30102 Red LED channel
 *             (HR-only mode 0x02 — LED1/Red is the active channel)
 *
 * Notes:
 *   - MAX30102 register 0x09 = 0x02 → Heart Rate mode (LED1 = Red only).
 *     The spec says "IR FIFO" but in HR mode the active channel is Red.
 *     Python ingestion treats this as generic PPG — LED color irrelevant
 *     for peak detection.
 *   - LED current set to ~25 mA (ledBrightness = 127 ≈ 25 mA).
 *   - Increase ledBrightness to 200 (~40 mA) if signal is weak.
 *   - Accel range ±4 g; gyro range ±500 dps.
 *   - 100 Hz → 10 ms inter-packet interval.  At 115200 baud each ~60-char
 *     line takes <6 ms, leaving comfortable headroom.
 */

#include <Wire.h>
#include "ICM42688.h"   // SparkFun ICM-42688-P v1.0.8
#include "MAX30105.h"   // SparkFun MAX3010x v1.1.2

// ── Hardware objects ────────────────────────────────────────────────────────
ICM42688  imu(Wire, 0x68);
MAX30105  ppgSensor;

// ── Timing ──────────────────────────────────────────────────────────────────
static const uint32_t SAMPLE_INTERVAL_US = 10000UL; // 100 Hz
static uint32_t       lastSampleUs       = 0;

// ── Sequence counter (wraps at 65535 → 0) ───────────────────────────────────
static uint16_t seqCounter = 0;

// ── LED brightness: 127 ≈ 25 mA, 200 ≈ 40 mA, 250 ≈ 50 mA ─────────────────
static const byte LED_BRIGHTNESS = 127;

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  while (!Serial) { ; }   // Wait for USB-CDC on Leonardo/Pro Micro boards

  Wire.begin();
  Wire.setClock(400000);  // 400 kHz fast-mode

  // ── ICM-42688-P ──────────────────────────────────────────────────────────
  if (imu.begin() < 0) {
    Serial.println("ERR:IMU_INIT");
    while (true) { delay(1000); }
  }
  imu.setAccelODR(ICM42688::odr100);        // 100 Hz ODR
  imu.setGyroODR(ICM42688::odr100);
  imu.setAccelFS(ICM42688::gpm4);           // ±4 g
  imu.setGyroFS(ICM42688::dps500);          // ±500 dps

  // ── MAX30102 ─────────────────────────────────────────────────────────────
  if (!ppgSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("ERR:PPG_INIT");
    while (true) { delay(1000); }
  }

  /*
   * setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange)
   *   ledMode = 1  → Heart Rate only (sets MODE register 0x09 = 0x02, Red LED)
   *   sampleRate = 100 Hz
   *   pulseWidth = 411 µs  (highest resolution, SNR-optimal at 100 Hz)
   *   adcRange   = 4096 nA full-scale
   */
  ppgSensor.setup(
    LED_BRIGHTNESS,   // LED current
    1,                // sampleAverage: no averaging — we want raw 100 Hz
    1,                // ledMode: 1 = Heart Rate (Red only, register 0x09 = 0x02)
    100,              // sampleRate Hz
    411,              // pulseWidth µs
    4096              // adcRange
  );

  // Confirm MODE register is 0x02 (belt-and-suspenders)
  ppgSensor.writeRegister8(0x57, 0x09, 0x02);

  // Transmit CSV header so Python parser can validate column order
  Serial.println("seq,ts_ms,ax,ay,az,gx,gy,gz,ppg");

  lastSampleUs = micros();
}

// ─────────────────────────────────────────────────────────────────────────────
void loop() {
  uint32_t now = micros();

  // Drift-free timing: advance by fixed interval each tick
  if (now - lastSampleUs < SAMPLE_INTERVAL_US) return;
  lastSampleUs += SAMPLE_INTERVAL_US;

  // ── Read ICM-42688-P ──────────────────────────────────────────────────────
  imu.readSensor();

  float ax = imu.getAccelX_mss();   // m/s²
  float ay = imu.getAccelY_mss();
  float az = imu.getAccelZ_mss();
  float gx = imu.getGyroX_rads() * 57.29578f;  // rad/s → deg/s
  float gy = imu.getGyroY_rads() * 57.29578f;
  float gz = imu.getGyroZ_rads() * 57.29578f;

  // ── Read MAX30102 FIFO ────────────────────────────────────────────────────
  uint32_t ppgValue = 0;
  ppgSensor.check();                     // Drain hardware FIFO into lib buffer
  while (ppgSensor.available()) {
    ppgValue = ppgSensor.getFIFORed();   // Red channel (HR mode active channel)
    ppgSensor.nextSample();
  }
  // ppgValue holds the most recent sample (later samples overwrite earlier ones
  // if the ISR-free loop was briefly late — acceptable at 100 Hz)

  // ── Transmit CSV packet ───────────────────────────────────────────────────
  Serial.print(seqCounter);
  Serial.print(',');
  Serial.print(millis());
  Serial.print(',');
  Serial.print(ax, 4);
  Serial.print(',');
  Serial.print(ay, 4);
  Serial.print(',');
  Serial.print(az, 4);
  Serial.print(',');
  Serial.print(gx, 3);
  Serial.print(',');
  Serial.print(gy, 3);
  Serial.print(',');
  Serial.print(gz, 3);
  Serial.print(',');
  Serial.println(ppgValue);

  seqCounter++;   // uint16 wraps naturally at 65535 → 0
}
