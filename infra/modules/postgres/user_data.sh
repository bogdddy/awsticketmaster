#!/bin/bash
set -euxo pipefail

DB_NAME="${db_name}"
DB_USER="${db_user}"
DB_PASS="${db_password}"
PROJECT_NAME="${project_name}"
VPC_CIDR="${vpc_cidr}"

dnf update -y
dnf install -y postgresql16-server postgresql16-contrib awscli nvme-cli

# Encontrar el volumen EBS adicional (el que NO es el root)
ROOT_DEV=$(lsblk -no PKNAME $(findmnt -n -o SOURCE /) 2>/dev/null || echo "")
if [ -z "$ROOT_DEV" ]; then
    ROOT_DEV=$(lsblk -ndo NAME | head -1)
fi
DATA_DEV=""
for dev in $(lsblk -ndo NAME); do
    if [ "$dev" != "$ROOT_DEV" ] && [[ "$dev" == nvme* || "$dev" == xvd* ]]; then
        DATA_DEV="$dev"
        break
    fi
done
# Fallback: usar xvdf si no se detectó
if [ -z "$DATA_DEV" ]; then
    DATA_DEV="xvdf"
fi
DEVICE="/dev/$DATA_DEV"

mkfs.xfs "$DEVICE"
mkdir -p /pgsqldata
mount "$DEVICE" /pgsqldata
echo "$DEVICE /pgsqldata xfs defaults,nofail 0 2" >> /etc/fstab

chown postgres:postgres /pgsqldata
su - postgres -c "/usr/bin/initdb -D /pgsqldata/data --encoding=UTF8"

cat >> /pgsqldata/data/postgresql.conf << 'PGCONF'
listen_addresses = '*'
port = 5432
max_connections = 100
shared_buffers = 512MB
effective_cache_size = 1GB
maintenance_work_mem = 128MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 5120kB
min_wal_size = 1GB
max_wal_size = 4GB
max_worker_processes = 8
max_parallel_workers_per_gather = 4
max_parallel_workers = 8
PGCONF

cat > /pgsqldata/data/pg_hba.conf << PGHBA
local   all   all                    peer
host    all   all   127.0.0.1/32     scram-sha-256
host    all   all   ${vpc_cidr}      scram-sha-256
PGHBA

rm -rf /var/lib/pgsql/data
ln -s /pgsqldata/data /var/lib/pgsql/data

systemctl enable postgresql
systemctl start postgresql 2>/dev/null || systemctl enable --now postgresql-16 2>/dev/null || true

for i in $(seq 1 30); do
    if su - postgres -c "pg_isready -q" 2>/dev/null; then
        break
    fi
    sleep 2
done

su - postgres -c "psql -c \"CREATE USER $${DB_USER} WITH PASSWORD '$${DB_PASS}';\""
su - postgres -c "psql -c \"CREATE DATABASE $${DB_NAME} OWNER $${DB_USER};\""
su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE $${DB_NAME} TO $${DB_USER};\""

su - postgres -c "psql -d $${DB_NAME} << 'SQL'
CREATE TABLE IF NOT EXISTS events (
    event_id    SERIAL PRIMARY KEY,
    mode        VARCHAR(20) NOT NULL CHECK (mode IN ('numbered', 'unnumbered')),
    capacity    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS seats (
    seat_id     INTEGER NOT NULL,
    event_id    INTEGER NOT NULL REFERENCES events(event_id),
    status      VARCHAR(20) NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'reserved', 'sold')),
    request_id  UUID,
    reserved_at TIMESTAMPTZ,
    sold_at     TIMESTAMPTZ,
    PRIMARY KEY (event_id, seat_id)
);

CREATE TABLE IF NOT EXISTS inventory (
    event_id    INTEGER PRIMARY KEY REFERENCES events(event_id),
    capacity    INTEGER NOT NULL,
    sold        INTEGER NOT NULL DEFAULT 0,
    CHECK (sold <= capacity)
);

CREATE TABLE IF NOT EXISTS processed (
    request_id   UUID PRIMARY KEY,
    result       VARCHAR(20) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS results (
    request_id   UUID PRIMARY KEY REFERENCES processed(request_id),
    enqueue_ts   TIMESTAMPTZ NOT NULL,
    start_ts     TIMESTAMPTZ,
    finish_ts    TIMESTAMPTZ,
    outcome      VARCHAR(20) NOT NULL,
    worker_id    VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_seats_status ON seats(status);
CREATE INDEX IF NOT EXISTS idx_results_finish ON results(finish_ts);

INSERT INTO events (event_id, mode, capacity) VALUES (1, 'numbered', 100000)
    ON CONFLICT (event_id) DO NOTHING;

INSERT INTO seats (seat_id, event_id, status)
SELECT generate_series(1, 100000), 1, 'available'
WHERE NOT EXISTS (SELECT 1 FROM seats WHERE event_id = 1 LIMIT 1);

INSERT INTO events (event_id, mode, capacity) VALUES (2, 'unnumbered', 100000)
    ON CONFLICT (event_id) DO NOTHING;

INSERT INTO inventory (event_id, capacity, sold) VALUES (2, 100000, 0)
    ON CONFLICT (event_id) DO NOTHING;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO $${DB_USER};
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO $${DB_USER};
GRANT USAGE ON SCHEMA public TO $${DB_USER};
SQL"

echo "PostgreSQL setup complete"
