#!/bin/bash
set -euxo pipefail

RABBITMQ_USER="${rabbitmq_user}"
RABBITMQ_PASS="${rabbitmq_password}"
PROJECT_NAME="${project_name}"

dnf update -y
dnf install -y socat logrotate awscli

cat > /etc/yum.repos.d/rabbitmq.repo << 'RMQREPO'
[rabbitmq_erlang]
name=rabbitmq_erlang
baseurl=https://packagecloud.io/rabbitmq/erlang/el/9/$basearch
repo_gpgcheck=1
gpgcheck=0
enabled=1
gpgkey=https://packagecloud.io/rabbitmq/erlang/gpgkey
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
metadata_expire=300

[rabbitmq_server]
name=rabbitmq_server
baseurl=https://packagecloud.io/rabbitmq/rabbitmq-server/el/9/$basearch
repo_gpgcheck=1
gpgcheck=0
enabled=1
gpgkey=https://packagecloud.io/rabbitmq/rabbitmq-server/gpgkey
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
metadata_expire=300
RMQREPO

dnf install -y erlang rabbitmq-server

rabbitmq-plugins enable rabbitmq_management

cat > /etc/rabbitmq/rabbitmq.conf << 'RABBITCONF'
loopback_users.guest = false
listeners.tcp.default = 5672
management.tcp.port = 15672
management.load_definitions = /etc/rabbitmq/definitions/definitions.json
vm_memory_high_watermark.relative = 0.8
cluster_formation.peer_discovery_backend = rabbit_peer_discovery_classic_config
RABBITCONF

mkdir -p /etc/rabbitmq/definitions

cat > /etc/rabbitmq/definitions/definitions.json << 'DEFS'
{
  "rabbit_version": "3.13.7",
  "users": [],
  "vhosts": [{"name": "/"}],
  "permissions": [],
  "topic_permissions": [],
  "parameters": [],
  "global_parameters": [{"name": "cluster_name", "value": "awsticket"}],
  "policies": [
    {
      "name": "dlx-tickets",
      "vhost": "/",
      "pattern": "^tickets\\\\.buy$",
      "apply-to": "queues",
      "definition": {
        "dead-letter-exchange": "tickets.dlx",
        "dead-letter-routing-key": ""
      },
      "priority": 1
    }
  ],
  "queues": [
    {"name": "tickets.buy", "vhost": "/", "durable": true, "auto_delete": false, "arguments": {"x-queue-type": "quorum"}},
    {"name": "tickets.dlq", "vhost": "/", "durable": true, "auto_delete": false, "arguments": {"x-queue-type": "quorum"}}
  ],
  "exchanges": [
    {"name": "tickets", "vhost": "/", "type": "direct", "durable": true, "auto_delete": false, "internal": false},
    {"name": "tickets.dlx", "vhost": "/", "type": "direct", "durable": true, "auto_delete": false, "internal": false}
  ],
  "bindings": [
    {"source": "tickets", "vhost": "/", "destination": "tickets.buy", "destination_type": "queue", "routing_key": "buy", "arguments": {}},
    {"source": "tickets.dlx", "vhost": "/", "destination": "tickets.dlq", "destination_type": "queue", "routing_key": "", "arguments": {}}
  ]
}
DEFS

systemctl enable rabbitmq-server
systemctl start rabbitmq-server

for i in $(seq 1 30); do
    if rabbitmqctl await_startup 2>/dev/null; then
        break
    fi
    sleep 2
done

rabbitmqctl add_user "$${RABBITMQ_USER}" "$${RABBITMQ_PASS}"
rabbitmqctl set_user_tags "$${RABBITMQ_USER}" administrator
rabbitmqctl set_permissions -p / "$${RABBITMQ_USER}" ".*" ".*" ".*"
rabbitmqctl delete_user guest 2>/dev/null || true

echo "RabbitMQ setup complete"
