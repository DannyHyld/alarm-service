-- Skema for alarm-servicen (Condition Monitoring).
-- MySQL kører filen automatisk første gang containeren starter.

CREATE TABLE IF NOT EXISTS events (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_type   VARCHAR(40)  NOT NULL,   -- SensorValueReceived, AnomalyDetected, AlarmRaised ...
    turbine_id   VARCHAR(40)  NOT NULL,
    payload      JSON         NOT NULL,
    occurred_at  DATETIME(3)  NOT NULL,   -- UTC
    INDEX idx_events_turbine (turbine_id, occurred_at),
    INDEX idx_events_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS alarms (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    turbine_id       VARCHAR(40)  NOT NULL,
    sensor_type      VARCHAR(40)  NOT NULL,
    value            DOUBLE       NOT NULL,
    threshold        DOUBLE       NOT NULL,
    priority         ENUM('høj', 'kritisk') NOT NULL,
    status           ENUM('raised', 'acknowledged') NOT NULL DEFAULT 'raised',
    reason           VARCHAR(255) NOT NULL,
    measured_at      DATETIME(3)  NOT NULL,
    raised_at        DATETIME(3)  NOT NULL,
    response_due_at  DATETIME(3)  NOT NULL,   -- svarfrist fra serviceaftalen
    acknowledged_at  DATETIME(3)  NULL,
    acknowledged_by  VARCHAR(60)  NULL,
    INDEX idx_alarms_status (status),
    INDEX idx_alarms_turbine (turbine_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
