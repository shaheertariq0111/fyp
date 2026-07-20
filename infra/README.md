# Phase 7 infrastructure

This directory contains the Phase 7 infrastructure-as-code for the FastAPI backend public API path:

```text
API Gateway HTTP API -> VPC Link -> Cloud Map -> ECS Fargate backend
```

The template intentionally does not create an Application Load Balancer, Route 53 record, ACM certificate, Amplify app, or AgentCore Runtime.

## Files

- `phase7-ecs-api.yaml` - CloudFormation template for ECR, ECS, Cloud Map, API Gateway, security groups, log groups, optional AgentRequests DynamoDB table, and least-privilege IAM roles.
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
- Existing DynamoDB application tables unless this stack is explicitly creating `AgentRequests`.
- Existing Secrets Manager secrets for sensitive config values if you plan to inject them during stack deployment.
- `AGENTCORE_RUNTIME_ARN` can remain empty until AgentCore Runtime is created later.
- `AgentRuntimeRepositoryUri` is an output from this stack and should be used for the Phase 6 AgentCore runtime image.
- `AgentCoreExecutionRoleArn` is an output from this stack and should be used when creating AgentCore Runtime later.
- `AgentCoreMemoryArn` can remain empty until AgentCore Memory is created.
- `AgentCoreSessionTokenSecretArn` should be set only if the AgentCore runtime keeps menu-link token creation enabled. Pass the Secrets Manager ARN, not the secret value.

## IAM roles

The template creates three separate role families:

- ECS task execution role: ECR image pull, CloudWatch container log delivery, and resource-specific Secrets Manager injection.
- ECS application task role: approved DynamoDB tables and optional AgentCore Runtime invocation. Secrets are injected by ECS and are not read directly by the application role. Application metrics are derived from CloudWatch Logs metric filters in the Phase 11 monitoring template, so the task role does not need `cloudwatch:PutMetricData`.
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
