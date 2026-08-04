# AWS Resources

## Region

Use `us-east-1`.

## Backend Stack

Defined in `infra/phase7-ecs-api.yaml`.

Creates or configures:

- ECR backend repository
- ECR AgentCore runtime repository
- ECS cluster with Container Insights
- ECS Fargate task definition and service
- public-subnet ECS networking for low-cost development
- ECS security group restricted to VPC Link ingress on port `8000`
- API Gateway HTTP API
- API Gateway `$default` stage and access logs
- API Gateway VPC Link
- Cloud Map private namespace and SRV service
- CloudWatch log groups
- dedicated encrypted temporary voice-media S3 bucket with one-day cleanup
- encrypted WhatsApp voice-processing SQS queue and dead-letter queue
- optional AgentRequests DynamoDB table
- ECS task execution role
- ECS application task role
- AgentCore execution role

Does not create:

- Application Load Balancer
- Route 53
- ACM certificate
- custom domain
- Amplify app
- AgentCore Runtime
- inbound voice worker or Lambda trigger

The voice bucket accepts temporary audio only under `voice-input/`. The bucket,
queue, and dead-letter queue are readiness resources and do not activate voice
processing; the ECS feature flag defaults to `false`.

Voice infrastructure safety details:

- The main queue retains jobs for one day, uses 20-second long polling and a
  300-second visibility timeout, and moves a message to the 14-day DLQ after
  three receives.
- The bucket uses explicit `DeletionPolicy: Delete` and
  `UpdateReplacePolicy: Delete` because disposable customer audio must not be
  deliberately retained. Stack deletion can fail safely if the bucket is not
  empty; later application code must delete objects promptly, with the one-day
  lifecycle as fallback.
- `voice-input/*` is the bounded object-resource wildcard needed for unique
  temporary object keys. The task role has no access outside that prefix.
- `transcription-job/${ProjectName}-whatsapp-voice-*` is the bounded wildcard
  for unique project-owned job names used by get/delete operations.
- `StartTranscriptionJob` alone uses `Resource: "*"` because that create action
  does not support resource-level authorization. It is isolated from get/delete.
- The bucket policy's `Principal: "*"`, `Action: s3:*`, and bucket-wide object
  wildcard occur only in an explicit deny when `aws:SecureTransport=false`;
  they grant no access.

## Secrets

Created manually in Secrets Manager:

- `fyp-dev/session-token-secret`
- `fyp-dev/admin-password`
- `fyp-dev/admin-session-secret`

## Amplify

Created manually in the AWS Amplify console with GitHub connection and monorepo app root `frontend`.

## AgentCore

AgentCore Runtime is created manually after:

- runtime image exists in ECR
- `AgentCoreExecutionRoleArn` is known
- memory exists if using AgentCore Memory
- environment JSON uses ARNs and names only, not secret values

## Monitoring Stack

Defined in `infra/phase11-monitoring.yaml`.

Creates:

- API Gateway 5xx alarm
- API Gateway latency alarm
- DynamoDB throttling alarm
- backend agent request failure metric filter and alarm
- backend AgentCore invocation failure metric filter and alarm
- conditional AgentCore runtime failure metric filter and alarm
- conditional ECS RunningTaskCount alarm

No SNS topic or notification action is configured by default. Alarms will not send notifications unless `AlarmActions` is supplied.

## Cost Categories

Expected recurring cost categories:

- Amplify Hosting build and hosting minutes
- API Gateway HTTP API requests
- VPC Link hourly cost
- ECS Fargate vCPU and memory
- ECR storage
- CloudWatch Logs ingestion and storage
- CloudWatch metrics and alarms
- DynamoDB read/write/storage/TTL-related usage
- temporary S3 storage and SQS request usage after voice processing is enabled
- Amazon Transcribe usage after voice processing is enabled
- Secrets Manager per-secret monthly charge
- Bedrock model usage
- AgentCore Runtime and Memory usage
