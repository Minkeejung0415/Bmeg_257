/*
 * caffeine_sensor.ino
 *
 * Hardware:
 *   - SparkFun ICM-42688-P (lib v1.1.x) on I2C 0x68
 *   - SparkFun MAX30102 / MAX3010x (lib v1.1.2) on I2C 0x57
 *   - MCU: Arduino-compatible board or ESP32
 *   - Both on Wire (I2C), 400 kHz
 *
 * Output (USB serial, 115200 baud):
 *   ASCII CSV, one line per sample at 100 Hz
 *   Header:  seq,ts_ms,ax,ay,az,gx,gy,gz,ppg
 */

#include <Wire.h>
#include "ICM42688.h"
#include "MAX30105.h"

// Hardware objects
ICM42688 imu(Wire, 0x68);
MAX30105 ppgSensor;

// Timing
static const uint32_t SAMPLE_INTERVAL_US = 10000UL; // 100 Hz
static uint32_t lastSampleUs = 0;

// Sequence counter
static uint16_t seqCounter = 0;

// LED brightness
static const byte LED_BRIGHTNESS = 127;
static constexpr float G_TO_MSS = 9.80665f;

void setup() {
  Serial.begin(115200);

#if defined(ARDUINO_ARCH_ESP32)
  delay(250);
#else
  while (!Serial) { ; }
#endif

#if defined(ARDUINO_ARCH_ESP32)
  Wire.begin(SDA, SCL);
#else
  Wire.begin();
#endif
  Wire.setClock(400000);

  if (imu.begin() < 0) {
    Serial.println("ERR:IMU_INIT");
    while (true) { delay(1000); }
  }
  imu.setAccelODR(ICM42688::odr100);
  imu.setGyroODR(ICM42688::odr100);
  imu.setAccelFS(ICM42688::gpm4);
  imu.setGyroFS(ICM42688::dps500);

  if (!ppgSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("ERR:PPG_INIT");
    while (true) { delay(1000); }
  }

  ppgSensor.setup(
    LED_BRIGHTNESS, // LED current
    1,              // sampleAverage
    1,              // ledMode: HR mode (Red)
    100,            // sampleRate
    411,            // pulseWidth
    4096            // adcRange
  );

  ppgSensor.writeRegister8(0x57, 0x09, 0x02);

  Serial.println("seq,ts_ms,ax,ay,az,gx,gy,gz,ppg");
  lastSampleUs = micros();
}

void loop() {
  uint32_t now = micros();
  if (now - lastSampleUs < SAMPLE_INTERVAL_US) return;
  lastSampleUs += SAMPLE_INTERVAL_US;

  if (imu.getAGT() < 0) return;

  float ax = imu.accX() * G_TO_MSS;
  float ay = imu.accY() * G_TO_MSS;
  float az = imu.accZ() * G_TO_MSS;
  float gx = imu.gyrX();
  float gy = imu.gyrY();
  float gz = imu.gyrZ();

  uint32_t ppgValue = 0;
  ppgSensor.check();
  while (ppgSensor.available()) {
    ppgValue = ppgSensor.getFIFORed();
    ppgSensor.nextSample();
  }

  Serial.print(seqCounter); Serial.print(',');
  Serial.print(millis());   Serial.print(',');
  Serial.print(ax, 4);      Serial.print(',');
  Serial.print(ay, 4);      Serial.print(',');
  Serial.print(az, 4);      Serial.print(',');
  Serial.print(gx, 3);      Serial.print(',');
  Serial.print(gy, 3);      Serial.print(',');
  Serial.print(gz, 3);      Serial.print(',');
  Serial.println(ppgValue);

  seqCounter++;
}
