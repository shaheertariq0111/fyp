# Phase 7 infrastructure

This directory contains the Phase 7 infrastructure-as-code for the FastAPI backend public API path:

```text
API Gateway HTTP API -> VPC Link -> Cloud Map -> ECS Fargate backend
```

The same stack also defines a separate durable WhatsApp voice worker path:

```text
Standard SQS queue -> ECS Fargate WhatsApp voice worker -> AgentCore / Agentflo
                          |
                          +-> dedicated DynamoDB voice-jobs table
```

The worker uses the same backend image but a separate task definition, execution
role, application role, security group, log group, and ECS service. It has no
port mapping, load balancer, Cloud Map registration, or API Gateway integration.
`WorkerDesiredCount` defaults to `0`, and `WhatsAppVoiceEnabled` remains `false`,
so Pass B1 creates disabled infrastructure without activating voice handling.

The template intentionally does not create an Application Load Balancer, Route 53 record, ACM certificate, Amplify app, or AgentCore Runtime.

## Files

- `phase7-ecs-api.yaml` - CloudFormation template for ECR, the web and disabled voice-worker ECS services, Cloud Map, API Gateway, security groups, log groups, durable voice-job storage, optional application DynamoDB tables, and least-privilege IAM roles.
- `phase13-github-oidc.yaml` - CloudFormation template for the GitHub Actions OIDC provider selection and separate backend/AgentCore deployment roles. Do not deploy until the account has been checked for an existing GitHub OIDC provider.
- `phase14-knowledge-base.yaml` - CloudFormation template for the separate Amazon Bedrock Knowledge Base stack, S3 approved-documents bucket, Amazon S3 Vectors bucket/index, data source, and Knowledge Base service role.
- `parameters/dev.example.json` - placeholder development parameters. Replace placeholders locally before deploying. Do not commit real secret ARNs, account IDs, image URIs, or generated values.
- `parameters/dev.knowledge-base.example.json` - placeholder development parameters for the Knowledge Base stack.
- `secrets-and-config.md` - Phase 9 secret creation, ECS configuration, Amplify configuration, and AgentCore Runtime configuration notes.

## Prerequisites

- AWS region: `us-east-1`
- An existing VPC with public subnets for low-cost development ECS tasks and API Gateway VPC Link ENIs.
- Public subnets must route outbound internet traffic through an internet gateway so ECS tasks with public IPs can reach ECR, CloudWatch Logs, DynamoDB, Secrets Manager, Bedrock, and AgentCore as needed.
- The ECS service remains privately reached by API Gateway through VPC Link, Cloud Map service discovery, and an ECS security group that only allows inbound TCP `8000` from the VPC Link security group.
- The Cloud Map service uses an `SRV` record, and the ECS service registry pins the `backend` container on port `8000`.
- A backend container image URI. You can create the stack with `DesiredCount=0` before an image is pushed, then update it later.
- Existing DynamoDB application tables unless this stack is explicitly creating them. The dedicated `WhatsAppVoiceJobs` table is always CloudFormation-managed by this stack.
- Existing Secrets Manager secrets for sensitive config values if you plan to inject them during stack deployment.
- `AGENTCORE_RUNTIME_ARN` can remain empty until AgentCore Runtime is created later.
- `AgentRuntimeRepositoryUri` is an output from this stack and should be used for the Phase 6 AgentCore runtime image.
- `AgentCoreExecutionRoleArn` is an output from this stack and should be used when creating AgentCore Runtime later.
- `AgentCoreMemoryArn` can remain empty until AgentCore Memory is created.
- `AgentCoreSessionTokenSecretArn` should be set only if the AgentCore runtime keeps menu-link token creation enabled. Pass the Secrets Manager ARN, not the secret value.

## Disabled WhatsApp voice-worker infrastructure

Pass B1 preserves the existing Standard processing queue, Standard dead-letter
queue, and temporary encrypted S3 bucket under their existing logical IDs and
physical names. The new jobs table stores temporary durable processing state,
leases, retry timing, and opaque request references; it does not store transcripts.

The media hostname and transcription language mode are intentionally unresolved.
Do not increase `WorkerDesiredCount` or change `WhatsAppVoiceEnabled` until a later
controlled activation has supplied and verified those provider values.

After Pass B1 infrastructure is manually deployed and verified, Phase 13 OIDC must
be manually updated before worker task-definition deployment is enabled. The
non-secret GitHub `staging` Environment variable `VOICE_WORKER_DEPLOY_ENABLED`
should remain absent or not equal to `true` until then. Setting it to the exact
string `true` only lets the backend workflow deploy the shared image to the worker
task definition; it does not change `WorkerDesiredCount`, enable
`WHATSAPP_VOICE_ENABLED`, or activate voice processing. Phase 11 worker monitoring
is separately gated by `WhatsAppVoiceMonitoringEnabled`, which defaults to `false`.

## IAM roles

The template creates separate role families:

- ECS task execution role: ECR image pull, CloudWatch container log delivery, and resource-specific Secrets Manager injection.
- ECS application task role: approved DynamoDB tables and optional AgentCore Runtime invocation. Secrets are injected by ECS and are not read directly by the application role. Application metrics are derived from CloudWatch Logs metric filters in the Phase 11 monitoring template, so the task role does not need `cloudwatch:PutMetricData`.
- Voice-worker execution role: pulls the existing backend image, writes only to the worker log group, and injects only the session-token and Agentflo gateway API-key secrets when configured. It cannot read admin or webhook secrets.
- Voice-worker application role: consumes and recovers the Standard voice queue, accesses temporary voice input, manages scoped Transcribe jobs, invokes the configured AgentCore runtime, and has a dedicated application-data policy. That policy can read/update the voice-job table and query only its `DueJobsIndex`; get/put/update agent requests; put conversation history; get/put customers and query only the customer `GSI1`; get and put customer-owned agent sessions by exact key; and query carts/orders to refresh response state after successful AgentCore writes. It has no menu, menu-session, audit, ticket, DLQ, wildcard DynamoDB action, wildcard DynamoDB resource permission, or global agent-session Scan permission. Menu, cart, order, customer, session-state, audit, and support tool operations invoked by the model execute under the separate AgentCore execution role.
- Optional voice-reply policy: grants only `polly:SynthesizeSpeech` to the voice-worker application role. AWS Polly does not support resource-level scoping for this action, so `Resource: "*"` is isolated in this one-action policy. The backend task role receives no Polly permission.

The worker execution role uses `Resource: "*"` only for the ECR authorization
token action, which AWS does not support as a repository-scoped permission. The
worker task role uses `Resource: "*"` only for
`transcribe:StartTranscriptionJob`; get/delete operations remain constrained to
`${ProjectName}-whatsapp-voice-*` job ARNs.

Cross-account Transcribe is optional. `VoiceTranscribeRoleArn` defaults to an
empty string, preserving the existing same-account client and permissions. When
configured, only the voice-worker application role may assume that exact ARN.
The primary bucket policy grants that external role only `s3:ListBucket` for
`voice-input/*` and `s3:GetObject` under `voice-input/*`. Upload and deletion
remain with the primary worker role. The role is an external prerequisite: this
stack does not create resources in the secondary account, and no permanent
secondary-account credentials are stored. Voice remains disabled until a
separate controlled activation.

## Optional outbound audio replies

`WhatsAppVoiceReplyEnabled` defaults to `false` independently of inbound
`WhatsAppVoiceEnabled`. When enabled, a voice job sends its normal text reply
first, synthesizes the same final text with Polly in the primary account, converts
MP3 to OGG Opus through the image's FFmpeg binary, and posts raw standard-base64
OGG bytes to Agentflo `/whatsapp/outbound`. No media-upload endpoint or generated
audio object is created. Polly streams are closed and FFmpeg uses memory pipes, so
generated audio is discarded after the one outbound attempt.

The converter explicitly requests a 16 kHz mono input resampling target with
`-ar 16000 -ac 1`. The required output is OGG/libopus, mono, 24 kbps, 20 ms frames,
with `application=voip`. Because Opus uses a fixed 48 kHz internal representation,
`ffprobe` is expected to report the output stream's `sample_rate` as 48000; this
does not mean the requested 16 kHz input resampling was omitted.
`AgentfloAudioFirestore` and `AgentfloAudioKinesis` default to the confirmed sample
values of `true` and are worker-only parameters. Polly voice, engine, optional
language, text/audio limits, and synthesis/conversion timeouts are also worker-only.
A failed or ambiguous optional audio attempt is logged and saved on the voice job
but does not undo text success or replay AgentCore.

Keep `WhatsAppVoiceReplyEnabled=false` during infrastructure and image rollout.
Before controlled activation, locally validate FFmpeg/ffprobe output and confirm
the selected Polly voice/engine/language and the Agentflo firestore/kinesis
semantics. Outbound audio can later be disabled without disabling inbound voice by
changing only `WhatsAppVoiceReplyEnabled` back to `false`.
- AgentCore execution role: fixed AgentCore service trust with source-account/source-ARN conditions, AgentCore runtime image pull from the agent-runtime ECR repository, Nova Pro inference-profile invocation, optional event-only AgentCore Memory access, approved DynamoDB table access for tools, optional Knowledge Base retrieval, and AgentCore runtime log writes.

The template does not attach `AdministratorAccess`, `AmazonBedrockFullAccess`, or `AmazonDynamoDBFullAccess`.

The Bedrock model policy is intentionally scoped to `us.amazon.nova-pro-v1:0`, its cross-region inference profile, and the Nova Pro foundation models in `us-east-1`, `us-east-2`, and `us-west-2`. It does not grant access to all Bedrock models.

## Local validation

This does not create AWS resources:

```powershell
aws cloudformation validate-template `
  --region us-east-1 `
  --template-body file://infra/phase7-ecs-api.yaml
```

Local linting for all infrastructure templates:

```powershell
cfn-lint `
  infra/phase3-network.yaml `
  infra/phase7-ecs-api.yaml `
  infra/phase11-monitoring.yaml `
  infra/phase13-github-oidc.yaml `
  infra/phase14-knowledge-base.yaml
```

The Knowledge Base template retains the approved-documents bucket, S3 Vectors bucket, and S3 Vectors index. Stack deletion will leave those stateful resources behind unless they are emptied and removed manually after an explicit review.

## Phase 14 Knowledge Base

The Phase 14 Knowledge Base stack is separate from the backend API stack.

- Stack name: `fyp-dev-knowledge-base`
- Template: `infra/phase14-knowledge-base.yaml`
- Region: `us-east-1`
- Purpose: separate Amazon Bedrock Knowledge Base infrastructure using S3 documents and Amazon S3 Vectors.

Local validation:

```powershell
& .\.venv\Scripts\cfn-lint.exe `
  infra\phase3-network.yaml `
  infra\phase7-ecs-api.yaml `
  infra\phase11-monitoring.yaml `
  infra\phase13-github-oidc.yaml `
  infra\phase14-knowledge-base.yaml
```

Authenticated read-only template validation:

```powershell
aws cloudformation validate-template `
  --region us-east-1 `
  --template-body file://infra/phase14-knowledge-base.yaml `
  --no-cli-pager
```

`validate-template` validates the template only. It does not create, update or delete AWS resources.

Future manual deployment command. This is documentation only; the stack has not been deployed yet. Deployment must occur only after PR merge and an approved deployment step.

`[CREATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-knowledge-base `
  --template-file infra/phase14-knowledge-base.yaml `
  --parameter-overrides ProjectName=fyp-dev `
  --capabilities CAPABILITY_NAMED_IAM `
  --no-fail-on-empty-changeset
```

Required stack outputs after deployment:

- `KnowledgeBaseId`
- `KnowledgeBaseArn`
- `DataSourceId`
- `KnowledgeDocumentsBucketName`
- `VectorBucketArn`
- `VectorIndexArn`
- `KnowledgeBaseServiceRoleArn`

Do not assume these output values exist before the stack has been deployed.

Manual ingestion is a later step after stack deployment:

1. Read the stack outputs.
2. Upload only approved corpus documents and metadata sidecars under the S3 `approved/` prefix.
3. Start a Bedrock Knowledge Base ingestion job.
4. Wait for completion.
5. Inspect failed-document warnings.
6. Test Bedrock Retrieve directly.
7. Do not connect the Knowledge Base to AgentCore until direct retrieval succeeds.

Do not commit actual bucket names, Knowledge Base IDs, data source IDs, ARNs or ingestion job IDs.

The documents bucket, S3 Vectors bucket and S3 Vectors index use `Retain` lifecycle policies. The Bedrock data source uses `DataDeletionPolicy: RETAIN`. Deleting the CloudFormation stack will not remove all retained data resources. Retained resources require deliberate manual cleanup, and S3 buckets may need to be emptied before deletion. This prevents accidental loss of the approved corpus and vector data.

S3 Vectors indexes used by Bedrock Knowledge Bases must configure `AMAZON_BEDROCK_TEXT` and `AMAZON_BEDROCK_METADATA` as non-filterable metadata. `MetadataConfiguration` is immutable and requires index replacement. The `restaurant-knowledge-index-v2` index name exists because the original empty index was created without this required configuration. The retained old index must only be removed manually after the replacement stack update and ingestion are verified.

The replacement Knowledge Base and data source use the `fyp_dev_restaurant_knowledge_v2` and `fyp_dev_restaurant_documents_v2` names because CloudFormation is replacing custom-named resources. The original Knowledge Base and data source are expected to be removed by CloudFormation after successful replacement. Stack outputs will provide the new `KnowledgeBaseId` and `DataSourceId` after deployment.

Phase 14 does not:

- Set the backend `KnowledgeBaseId` parameter
- Update AgentCore `KNOWLEDGE_BASE_ID`
- Upload documents
- Start ingestion
- Modify backend or frontend code

## Deployment command template

Do not run this until you are ready to create or update AWS resources.

`[CREATES OR UPDATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-backend-api `
  --template-file infra/phase7-ecs-api.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    ProjectName=fyp-dev `
    VpcId=<vpc-id> `
    PublicSubnetIds=<public-subnet-a>,<public-subnet-b> `
    BackendImageUri=<account-id>.dkr.ecr.us-east-1.amazonaws.com/fyp-dev-backend:<tag> `
    DesiredCount=0 `
    WorkerDesiredCount=0 `
    WhatsAppVoiceEnabled=false `
    VoiceTranscribeRoleArn= `
    FrontendCorsOrigins=https://<amplify-app>.amplifyapp.com `
    MenuSiteBaseUrl=https://<amplify-app>.amplifyapp.com/menu `
    AgentCoreRuntimeArn= `
    AgentCoreMemoryArn= `
    AgentCoreSessionTokenSecretArn=<session-token-secret-arn-if-menu-links-are-enabled> `
    SessionTokenSecretArn=<session-token-secret-arn> `
    AdminPasswordSecretArn=<admin-password-secret-arn> `
    AdminSessionSecretArn=<admin-session-secret-arn>
```

Expected success output:

```text
Successfully created/updated stack - fyp-dev-backend-api
```

After a backend image has been pushed and configuration is complete, update `DesiredCount` to `1` or higher.
