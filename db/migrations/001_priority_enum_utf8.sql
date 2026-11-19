-- Migrering 001 (19/11): ret ENUM-værdien 'høj' i databaser, der er oprettet,
-- før init.sql fik "SET NAMES utf8mb4". Der findes ingen rækker med 'høj',
-- fordi alle forsøg på at gemme dem fejlede, så ændringen mister ingen data.
--
-- Kør (lokalt):
--   docker compose exec -T mysql sh -c 'mysql --default-character-set=utf8mb4 -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' < db/migrations/001_priority_enum_utf8.sql
-- Kør (produktion, på VM'en i /opt/alarm-service): samme kommando med -f docker-compose.remote.yml

SET NAMES utf8mb4;
ALTER TABLE alarms MODIFY priority ENUM('høj', 'kritisk') NOT NULL;
