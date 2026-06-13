# Architecture Decision Log: Autoscaler Pivot

## Context

The autoscaler Lambda was originally triggered every 60s via an EventBridge
Rule (`schedule_expression = "rate(1 minute)"`, the minimum supported by
EventBridge Rules). The Lambda queried RabbitMQ Management API for backlog
and arrival rate, computed a desired worker count, and called
`ecs.update_service()`.

## Problem

Two issues prevented effective autoscaling:

### 1. EventBridge minimum interval

EventBridge Rules cannot fire more frequently than once per minute. Combined
with Fargate provisioning (60-90s), the total response time was ~120-150s.
For a ramp-up lasting 600s, the autoscaler had only ~4 evaluation cycles,
causing backlog to grow unchecked for 2-3 minutes.

### 2. Lambda VPC networking

The Lambda was attached to the public subnet (same as workers). However,
Lambda ENIs in a VPC do not receive public IPs. Without a NAT Gateway,
calls from `boto3` to AWS public endpoints (ECS, CloudWatch) would hang
until timeout. The autoscaler was effectively dead — workers never scaled.

### Measured impact on elasticity test

| Metric | Before fix |
|--------|-----------|
| p50 latency | 27s |
| p95 latency | 331s (~5.5 min) |
| Tickets sold | ~11,880 |
| Peak throughput | ~38 rps |
| Workers scaled | Never (always 4) |

## Solution

Three changes were made:

### 1. Trigger: EventBridge → SQS

Instead of a scheduled rule, each worker now publishes a message to an SQS
queue every ~15 seconds in a background daemon thread. The SQS queue is
configured as a Lambda event source mapping with `batch_size=1` and
`reserved_concurrent_executions=1` to prevent concurrent scaling decisions.

### 2. NAT Gateway

An Elastic IP + NAT Gateway were added in the public subnet, and the Lambda
was moved to a dedicated private subnet (`10.0.2.0/24`) with a route table
pointing `0.0.0.0/0` to the NAT Gateway. This gives the Lambda outbound
internet access to reach ECS, CloudWatch, and SQS APIs.

### 3. High-resolution metrics

`BacklogPerWorker` is now published with `StorageResolution=1` (high
resolution), enabling CloudWatch alarms with period as low as 10s.

## Result

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| p50 latency | 27s | 1.0s | 27× |
| p95 latency | 331s | 46s | 7× |
| Tickets sold | 11,880 | 15,900 | +31% |
| Peak throughput | 38 rps | 189 rps | 5× |
| Detection interval | 60s | ~15s | 4× |
| Total response lag | ~150s | ~75-105s | ~1.5× |

## Remaining limitation

Fargate provisioning (60-90s) remains the dominant term in total response
lag. Eliminating it would require pre-warming (keeping a pool of standby
workers) or switching to a faster compute platform.
