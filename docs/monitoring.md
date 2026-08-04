# Phase 11 Logging And Monitoring

Phase 11 monitoring is defined in:

```text
infra/phase11-monitoring.yaml
```

The monitoring stack is separate from the application stack. It must not recreate existing application log groups, API Gateway, ECS, AgentCore, DynamoDB, or IAM resources.

## Current Deployment Values

Use these values for the current `fyp-dev` staging deployment:

```text
Region: us-east-1
ProjectName: fyp-dev
HttpApiId: vogwq90ly8
ApiStageName: $default
EcsClusterName: fyp-dev-backend
EcsServiceName: fyp-dev-backend
BackendLogGroupName: /ecs/fyp-dev/backend
Api Gateway access log group: /aws/apigateway/fyp-dev/backend-http-api
AgentCoreLogGroupName: /aws/bedrock-agentcore/runtimes/fyp_dev_restaurant_agent-dwLwVnClBF-DEFAULT
MinimumRunningTaskCount: 1
WhatsAppVoiceMonitoringEnabled: false
```

The monitored DynamoDB tables are:

```text
fyp-dev-agent-requests
shaheer-fyp-agent-audit-events
shaheer-fyp-agent-sessions
shaheer-fyp-carts
shaheer-fyp-customers
shaheer-fyp-menu-sessions
shaheer-fyp-orders
shaheer-fyp-restaurant-menu
```

When voice monitoring is explicitly enabled, the dedicated jobs table is also
monitored as `fyp-dev-WhatsAppVoiceJobs`.

## Structured Logs

Backend and AgentCore runtime logs are JSON-formatted for CloudWatch Logs. Monitoring filters use only bounded fields such as:

```text
event
route
method
status_code
response_time_ms
agentcore_invocation_status
agent_request_status
tool_success
is_write
bedrock_response_time_ms
dynamodb_operation
dynamodb_table
error_code
channel
exception_type
```

Do not log message content, request bodies, passwords, cookies, authorization headers, session tokens, secret values, AWS credentials, raw customer addresses, or full private conversation text.

## Dashboard

The template creates a CloudWatch dashboard named:

```text
fyp-dev-monitoring
```

Dashboard widgets cover:

- API Gateway HTTP API `Count`, lowercase `4xx`, lowercase `5xx`, `Latency` p95, and `IntegrationLatency` p95.
- ECS service `CPUUtilization` and `MemoryUtilization` from `AWS/ECS` with `ClusterName` and `ServiceName` dimensions.
- ECS `RunningTaskCount` from `ECS/ContainerInsights`.
- DynamoDB total read and write throttling across all eight configured tables.
- Backend agent-request failures and backend AgentCore invocation failures.
- Backend HTTP response p95 latency and backend-to-AgentCore invocation p95 latency.
- AgentCore runtime invocation failures, tool execution failures, and runtime invocation p95 latency.
- Unexpected ECS task stops captured by the EventBridge rule.

The dashboard uses aggregate metrics only. It does not use request IDs, actor IDs, session IDs, tool names, error messages, or other high-cardinality dimensions.

The dashboard is intentionally unchanged for Pass B2. Its JSON body is a single
CloudFormation string and cannot safely conditionally include a worker section
without fragile substitutions. Conditional worker alarms provide the coverage while
the default dashboard remains valid whether or not Pass B1 exists.

## Alarms

The template creates these alarms:

- API Gateway HTTP API `5xx` sum greater than `Api5xxThreshold`, default `0`.
- API Gateway p95 latency using `ApiLatencyMetricName`, default `Latency`, greater than `ApiLatencyP95ThresholdMs`, default `5000`.
- ECS `RunningTaskCount` below `MinimumRunningTaskCount`; created only when `MinimumRunningTaskCount > 0`.
- ECS service average CPU greater than `EcsCpuUtilizationThreshold`, default `80`, for 3 of 3 one-minute periods.
- ECS service average memory greater than `EcsMemoryUtilizationThreshold`, default `80`, for 3 of 3 one-minute periods.
- DynamoDB `ReadThrottleEvents` summed across all eight tables, default threshold `0`.
- DynamoDB `WriteThrottleEvents` summed across all eight tables, default threshold `0`.
- Backend `agent_request_failed` log-derived failures, default threshold `0`.
- Backend `agentcore_invocation_failed` log-derived failures, default threshold `0`.
- Backend HTTP response p95 latency greater than `BackendHttpLatencyP95ThresholdMs`, default `2000`.
- Backend-to-AgentCore p95 latency greater than `BackendAgentCoreLatencyP95ThresholdMs`, default `90000`.
- AgentCore runtime invocation failures, runtime p95 latency, and tool execution failures when `AgentCoreLogGroupName` is non-empty.
- Unexpected ECS task stops captured by EventBridge, default threshold `0`.

No SNS topic or notification action is created by default. `AlarmActions` is optional; when empty, alarms can enter `ALARM` state but do not send notifications.

### Disabled WhatsApp voice-worker monitoring

`WhatsAppVoiceMonitoringEnabled` is a String parameter with allowed values `true`
and `false`, and defaults to `false`. With the default, no resource that depends on
the worker service or log group is created, so existing monitoring behavior is
preserved and no Pass B1 resource is required.

When enabled, the template adds:

- Log-derived `ManualReview`, `PermanentFailure`, and `RetryableFailure` metrics and
  alarms in `${ProjectName}/WhatsAppVoiceWorker`, using only the existing
  `voice_manual_review`, `voice_permanent_failure`, and `voice_retryable_failure`
  event names. No customer or payload field becomes a metric dimension.
- Processing-queue alarms for oldest visible message age and visible backlog.
- A DLQ visible-message alarm whose default threshold `0` detects any visible
  message. The template does not purge, redrive, or modify either queue.
- Worker CPU and memory alarms, plus a separate unexpected task-stop EventBridge
  capture and alarm scoped to `service:fyp-dev-whatsapp-voice-worker`. Normal
  `ServiceSchedulerInitiated` and `UserInitiated` stops are excluded.
- Separate read/write throttle alarms for `fyp-dev-WhatsAppVoiceJobs`; the eight
  existing DynamoDB tables and their aggregate alarms are unchanged.

All of these alarms use `TreatMissingData: notBreaching`, except the optional worker
running-task alarm. That alarm exists only when monitoring is enabled and
`MinimumVoiceWorkerRunningTaskCount` is greater than `0`; it is therefore absent
while the worker is intentionally at desired count `0`.

Queue age signals stalled processing, processing-queue depth signals backlog, and
any DLQ message signals work requiring investigation. A manual-review alarm means an
operator should inspect the privacy-safe job status and structured failure metadata,
resolve provider/configuration or processing causes, and use an approved recovery
procedure; it does not automatically retry, redrive, or expose customer content.

## Metric Filters

Backend log metric filters read from `/ecs/fyp-dev/backend`:

- `event = "agent_request_failed"` publishes `fyp-dev/Backend AgentRequestFailed`.
- `event = "agentcore_invocation_failed"` publishes `fyp-dev/Backend AgentCoreInvocationFailed`.
- `event = "http_request_completed"` with non-negative `response_time_ms` publishes raw `HttpResponseLatencyMs` datapoints.
- `event = "agentcore_invocation_completed"` with non-negative `response_time_ms` publishes raw `AgentCoreInvocationLatencyMs` datapoints.

AgentCore runtime filters are conditional on `AgentCoreLogGroupName`:

- `event = "agentcore_invocation_failed"` publishes `fyp-dev/AgentCore AgentCoreInvocationFailed`.
- `event = "agentcore_invocation_completed"` with non-negative `response_time_ms` publishes raw `AgentCoreInvocationLatencyMs` datapoints.
- `event = "agent_tool_completed"` and `tool_success = false` publishes `AgentCoreToolExecutionFailed`.

Latency filters publish raw millisecond datapoints without default zero values, so percentile statistics are not skewed by non-matching log events.

Tool failure metrics intentionally do not include `tool_name`, `error_code`, request IDs, actor IDs, or session IDs as CloudWatch metric dimensions.

## Unexpected ECS Task Stops

The template creates a dedicated log group:

```text
/aws/events/fyp-dev/ecs-task-stopped
```

The deployed backend task group is:

```text
service:fyp-dev-backend
```

An EventBridge rule captures ECS task state change events only for the configured cluster and backend service task group where:

```text
clusterArn = arn:aws:ecs:us-east-1:<account-id>:cluster/fyp-dev-backend
group = service:fyp-dev-backend
lastStatus = STOPPED
stopCode is not ServiceSchedulerInitiated
stopCode is not UserInitiated
```

This prevents unrelated tasks in the same ECS cluster from contributing to the alarm. It excludes normal ECS service replacement and user-initiated stops where practical, while still catching failure stop codes such as failed task starts and essential container exits. Some AWS stop reasons can vary, so this alarm is intentionally scoped by `group` and `stopCode` rather than free-form `stoppedReason` text.

The monitoring stack also creates the CloudWatch Logs resource policy required for EventBridge to write to the dedicated log group.

## Missing Data Behavior

The template uses these `TreatMissingData` values:

- ECS `RunningTaskCount`: `breaching` when the alarm exists. The alarm is not created while `MinimumRunningTaskCount=0`.
- API Gateway errors and latency: `notBreaching`.
- ECS CPU and memory: `notBreaching`.
- DynamoDB throttling: `notBreaching`.
- Log-derived failures and latency: `notBreaching`.
- Unexpected ECS task stops: `notBreaching`.

For bootstrap with the ECS service intentionally at `DesiredCount=0`, deploy or update the monitoring stack with `MinimumRunningTaskCount=0`. After the ECS service starts, update with `MinimumRunningTaskCount=1`; that update creates the running-task alarm.

Voice monitoring activation follows infrastructure activation: first deploy and
verify Pass B1 with `WorkerDesiredCount=0` and voice disabled; then update Phase 13
OIDC and optionally enable workflow integration; then update Phase 11 with
`WhatsAppVoiceMonitoringEnabled=true` and
`MinimumVoiceWorkerRunningTaskCount=0`. A nonzero worker minimum belongs only to the
later controlled worker activation. Monitoring does not enable the worker or inbound
voice processing.

## Log Retention

Do not declare existing application log groups as `AWS::Logs::LogGroup` resources in this monitoring stack.

Use idempotent AWS CLI commands to enforce 30-day retention on existing log groups. These commands update log group configuration only.

`[UPDATES AWS LOG RETENTION]`

```powershell
aws logs put-retention-policy `
  --region us-east-1 `
  --log-group-name "/ecs/fyp-dev/backend" `
  --retention-in-days 30

aws logs put-retention-policy `
  --region us-east-1 `
  --log-group-name "/aws/apigateway/fyp-dev/backend-http-api" `
  --retention-in-days 30

aws logs put-retention-policy `
  --region us-east-1 `
  --log-group-name "/aws/bedrock-agentcore/runtimes/fyp_dev_restaurant_agent-dwLwVnClBF-DEFAULT" `
  --retention-in-days 30
```

The backend and API Gateway log groups already use 30 days. The AgentCore runtime log group should be updated from unlimited retention to 30 days.

## Validation

Validate the template before deployment:

```powershell
aws cloudformation validate-template `
  --region us-east-1 `
  --template-body file://infra/phase11-monitoring.yaml
```

This command validates only. It does not create, update, or delete AWS resources.

## Manual Deployment

Run this only when the backend log group, API Gateway, ECS service, DynamoDB tables, and AgentCore Runtime log group exist.

`[CREATES OR UPDATES AWS MONITORING RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-monitoring `
  --template-file infra/phase11-monitoring.yaml `
  --parameter-overrides `
    ProjectName=fyp-dev `
    HttpApiId=vogwq90ly8 `
    ApiStageName='$default' `
    EcsClusterName=fyp-dev-backend `
    EcsServiceName=fyp-dev-backend `
    MinimumRunningTaskCount=1 `
    BackendLogGroupName=/ecs/fyp-dev/backend `
    AgentCoreLogGroupName=/aws/bedrock-agentcore/runtimes/fyp_dev_restaurant_agent-dwLwVnClBF-DEFAULT `
    AgentRequestsTableName=fyp-dev-agent-requests `
    MenuTableName=shaheer-fyp-restaurant-menu `
    CartsTableName=shaheer-fyp-carts `
    OrdersTableName=shaheer-fyp-orders `
    CustomersTableName=shaheer-fyp-customers `
    AgentSessionsTableName=shaheer-fyp-agent-sessions `
    MenuSessionsTableName=shaheer-fyp-menu-sessions `
    AuditTableName=shaheer-fyp-agent-audit-events
```

Expected success output:

```text
Successfully created/updated stack - fyp-dev-monitoring
```

The template remains the source of truth for Phase 11 monitoring resources. Avoid creating separate ad hoc alarms or metric filters unless the template is updated to match.
