# CI/CD

This repository has validation-only workflows and protected backend and AgentCore deployment workflows. The GitHub OIDC/IAM stack is configured as `fyp-dev-github-oidc`, and the GitHub `staging` environment is configured with a `deployment` branch restriction and a required reviewer.

## Workflows

### `.github/workflows/ci.yml`

Runs component validation for pull requests, pushes to any branch, and manual `workflow_dispatch` runs.

Permissions are limited to:

```yaml
contents: read
```

The workflow does not request `id-token: write`, does not use secrets, does not configure AWS credentials, does not push Docker images, and does not deploy ECS or AgentCore.

The stable branch-protection check is:

```text
CI / Quality gate
```

This is the explicit display name of the `quality-gate` job in `.github/workflows/ci.yml`.

Jobs are path-aware:

| Job | Triggering paths | Validation |
| --- | --- | --- |
| Backend | `backend/**`, `dominos_pakistan_menu_import.json`, `.github/workflows/deploy-backend.yml` | Install backend package, run backend tests, build `linux/amd64` backend image, start it, and check `http://localhost:8000/health`. |
| Agent runtime | `agent-runtime/**`, `backend/src/agent/**`, `backend/src/models/**`, `backend/src/services/**`, `backend/src/repositories/**`, `backend/src/infrastructure/**`, `backend/src/agent_client/schemas.py`, `backend/pyproject.toml`, `dominos_pakistan_menu_import.json`, `.github/workflows/deploy-agentcore.yml`, `.github/scripts/build-agentcore-update-input.py`, `.github/scripts/tests/**` | Install backend and runtime packages, run runtime tests, build `linux/arm64` image with `agent-runtime/Dockerfile`, start it through QEMU, and check `http://localhost:8080/ping`. |
| Frontend | `frontend/**`, `amplify.yml` | Use Node 20, run `npm ci`, `npm run typecheck`, `npm run lint`, and `npm run build`. |
| Infrastructure | `infra/**`, `docs/deployment.md`, `docs/cicd.md`, `infra/README.md`, `.github/workflows/validate-infra.yml` | Parse CloudFormation YAML locally and run `cfn-lint` against the four infrastructure templates. |

Changes to `.github/workflows/ci.yml` run every validation job. Manual `workflow_dispatch` runs all component jobs.

The final `Quality gate` job always runs. It requires the path-detection job to succeed and accepts only `success` or `skipped` for path-aware component jobs. It fails on `failure`, `cancelled`, `timed_out`, `action_required`, `neutral`, `stale`, empty, or any unexpected result.

The AgentCore runtime image uses the repository root as the Docker build context and `agent-runtime/Dockerfile`. Docker uses `agent-runtime/Dockerfile.dockerignore` for that Dockerfile-specific build context filtering.

### `.github/workflows/validate-infra.yml`

Runs local infrastructure validation for pull requests, pushes, and manual runs when infrastructure files change.

It validates:

- `infra/phase3-network.yaml`
- `infra/phase7-ecs-api.yaml`
- `infra/phase11-monitoring.yaml`
- `infra/phase13-github-oidc.yaml`

This workflow intentionally has no AWS authentication. It performs local YAML parsing and `cfn-lint` validation only, and does not run `aws cloudformation validate-template`, create change sets, or deploy stacks.

The standalone infrastructure workflow remains separate from the CI infrastructure job. The CI job exists so `CI / Quality gate` can cover infrastructure pull-request changes without requiring another branch-protection check.

### `.github/workflows/deploy-backend.yml`

Prepares automated deployment of the FastAPI backend to the existing ECS service. It runs only for:

- pushes to the `deployment` branch when `backend/**`, `dominos_pakistan_menu_import.json`, or `.github/workflows/deploy-backend.yml` changes
- manual `workflow_dispatch` runs

The workflow has two jobs:

| Job | Environment | AWS access | Purpose |
| --- | --- | --- | --- |
| `validate` | none | none | Check out the repository, set up Python 3.10, install backend dev dependencies, and run backend tests. |
| `deploy` | `staging` | GitHub OIDC only | Build and push the backend image, register a task-definition revision with only the backend container image changed, update the existing ECS service, wait for stability, and check the API Gateway `/health` endpoint. |

The deploy job uses these GitHub environment or repository variables:

```text
AWS_REGION=us-east-1
BACKEND_DEPLOY_ROLE_ARN=<BackendDeploymentRoleArn output>
VOICE_WORKER_DEPLOY_ENABLED=<optional GitHub staging Environment variable>
```

It builds exactly one immutable image tag per run:

```text
352306494518.dkr.ecr.us-east-1.amazonaws.com/fyp-dev-backend:${GITHUB_SHA}
```

It does not push a `latest` tag. The same immutable image is used for both the web
service and, when explicitly enabled, the voice-worker service. The workflow reads
each deployed task definition as the source of truth and replaces only the matching
container image.

The default remains web-only. Worker deployment steps run only when the non-secret
GitHub `staging` Environment variable `VOICE_WORKER_DEPLOY_ENABLED` is the exact
string `true`; an absent variable or any other value skips them. Before setting the
variable, the Pass B1 worker infrastructure and the Phase 13 OIDC update must exist
and be verified.

These are three independent controls:

1. `VOICE_WORKER_DEPLOY_ENABLED=true` lets the workflow update the worker task
   definition.
2. Phase 7 `WorkerDesiredCount` controls whether ECS runs worker tasks; this
   workflow never changes it.
3. Phase 7 `WhatsAppVoiceEnabled` / `WHATSAPP_VOICE_ENABLED` controls inbound voice
   enqueueing; this workflow never enables it.

The web deployment target remains:

```text
cluster: fyp-dev-backend
service: fyp-dev-backend
```

When worker integration is enabled, the workflow captures both previous task
definition ARNs before changing either service. It deploys and verifies the
default-zero worker first, then deploys and verifies the web service and checks the
public `/health` endpoint. Desired count `0` and running count `0` are valid for the
worker. The worker has no HTTP, API Gateway, load-balancer, Cloud Map, port, or
functional voice check.

If a worker or later web deployment/health step fails, bounded rollback steps try to
restore every service changed during the run to its captured task definition. They
update only `taskDefinition`, preserving each current desired count, wait at most ten
minutes per service, print service diagnostics, and leave the original job failed.

The deployment job intentionally does not run for pull requests, does not use static
AWS credentials, does not deploy AgentCore, and does not execute CloudFormation.

### `.github/workflows/deploy-agentcore.yml`

Prepares automated deployment of the AgentCore Runtime image to the existing runtime. It runs only for:

- pushes to the `deployment` branch when AgentCore image inputs change
- manual `workflow_dispatch` runs

Path filters are:

- `agent-runtime/**`
- `backend/src/agent/**`
- `backend/src/models/**`
- `backend/src/services/**`
- `backend/src/repositories/**`
- `backend/src/infrastructure/**`
- `backend/src/agent_client/schemas.py`
- `backend/pyproject.toml`
- `dominos_pakistan_menu_import.json`
- `.github/workflows/deploy-agentcore.yml`
- `.github/scripts/build-agentcore-update-input.py`
- `.github/scripts/tests/**`

The workflow has two jobs:

| Job | Environment | AWS access | Purpose |
| --- | --- | --- | --- |
| `validate` | none | none | Check out the repository, set up Python 3.10, install backend and AgentCore runtime dev dependencies, run runtime and helper tests, build the `linux/arm64` image through Buildx/QEMU, start it locally, and check `http://localhost:8080/ping`. |
| `deploy` | `staging` | GitHub OIDC only | Build and push the SHA-tagged `linux/arm64` image, capture the current AgentCore Runtime, construct a preserved update request, call `update-agent-runtime`, wait for `READY`, and verify runtime version, image URI, role, and network configuration. |

The deploy job uses this GitHub environment variable:

```text
AGENTCORE_DEPLOY_ROLE_ARN=arn:aws:iam::352306494518:role/fyp-github-agentcore-deployer
```

It also uses `AWS_REGION=us-east-1`.

It builds exactly one immutable image tag per run:

```text
352306494518.dkr.ecr.us-east-1.amazonaws.com/fyp-dev-agent-runtime:${GITHUB_SHA}
```

It does not push a `latest` tag or `deployment-latest` tag.

The workflow reads the existing runtime:

```text
runtime ID: fyp_dev_restaurant_agent-dwLwVnClBF
runtime ARN: arn:aws:bedrock-agentcore:us-east-1:352306494518:runtime/fyp_dev_restaurant_agent-dwLwVnClBF
execution role: arn:aws:iam::352306494518:role/fyp-dev-agentcore-execution
```

Normal deployment requires the runtime to be `READY` before update. The current runtime artifact must be container-based, and the runtime ARN and execution role must match the configured target.

`.github/scripts/build-agentcore-update-input.py` constructs `agentcore-update-input.json` for `aws bedrock-agentcore-control update-agent-runtime`. It includes:

- `agentRuntimeId`
- `agentRuntimeArtifact.containerConfiguration.containerUri`
- `roleArn`
- `networkConfiguration`
- `clientToken`

It preserves optional mutable runtime configuration only when present:

- `description`
- `authorizerConfiguration`
- `requestHeaderConfiguration`
- `protocolConfiguration`
- `lifecycleConfiguration`
- `metadataConfiguration`
- `environmentVariables`
- `filesystemConfigurations`

It excludes response-only fields such as runtime ARN, name, version, status, timestamps, failure reason, and workload identity details. It validates that the new image URI belongs to the expected ECR repository, that the role ARN remains unchanged, and that the network configuration remains unchanged.

The helper has two image-validation modes:

- `deployment` requires a full 40-character lowercase Git commit SHA, and the image tag must equal that SHA.
- `rollback` accepts any non-empty legacy or SHA tag in the exact configured ECR repository. This supports early manually deployed images that may use tags such as `dev-*`.

Both modes reject digest references and untagged image references.

For `networkConfiguration.networkModeConfig.requireServiceS3Endpoint`, the helper preserves the field only if it is already present in the current runtime response. It never invents the field. This avoids sending a field that can produce `ValidationException` for newer AgentCore runtimes while preserving the current configuration for older runtimes that already contain it.

Deployed functional invocation is intentionally deferred. The inspected `/invocations` contract accepts:

```json
{
  "message": "hello",
  "user_id": "synthetic-user",
  "agent_session_id": "synthetic-session-id-at-least-33-characters",
  "branch_id": "default",
  "channel": "web"
}
```

The backend AgentCore client also sends `runtimeSessionId` and `runtimeUserId`. The runtime immediately integrates AgentCore Memory and the restaurant agent, whose tools can touch menu, cart, order, customer, session, and audit data. Because the repository does not contain a proven non-mutating deployed invocation mode, the workflow verifies deployment with READY status plus image URI, runtime version, role, and network checks. The local `/ping` smoke test remains the executable container contract test.

The AgentCore deployment role intentionally lacks runtime invocation permission because deployed functional invocation is deferred.

AgentCore creates a new immutable runtime version during update. The DEFAULT endpoint follows the latest runtime version automatically. Existing active sessions may continue using the code version with which their microVM started until those sessions terminate.

## Release Flow

The intended flow after branch protection is enabled is:

```text
feature branch
-> pull request into deployment
-> CI / Quality gate
-> review and merge
-> path-aware protected staging deployment
-> reviewer approval
-> backend ECS and/or AgentCore deployment
-> frontend through Amplify
-> verified deployment
-> deployment merged into main
```

Path behavior:

- Backend-only changes run backend CI and, after merge to `deployment`, backend ECS deployment. AgentCore deployment also runs only when shared AgentCore-imported backend paths change.
- AgentCore-only changes run AgentCore validation and, after merge to `deployment`, AgentCore deployment.
- Frontend-only changes run frontend CI and deploy through Amplify from the `deployment` branch.
- Infrastructure-only changes run infrastructure validation only; CloudFormation is not deployed automatically.
- Documentation-only changes run only relevant validation. Backend or AgentCore deployment does not run unless a deployment workflow file itself changed.

`deployment` remains the source branch for staging deployments. `main` remains the stable synchronized branch. Merges into `main` do not deploy AWS resources. Deployment approval through the protected `staging` environment remains separate from pull-request approval.

After branch protection is enabled, avoid direct pushes.

Recommended `deployment` branch protection:

- require pull request
- require one approval
- require conversation resolution
- require `CI / Quality gate`
- block force pushes
- restrict deletion

Recommended `main` branch protection:

- require pull request
- require one approval
- require conversation resolution
- require `CI / Quality gate`
- block force pushes
- restrict deletion

## GitHub OIDC Preparation

`infra/phase13-github-oidc.yaml` defines the GitHub Actions OIDC authentication used by deployment workflows. The configured CloudFormation stack is `fyp-dev-github-oidc`.

OIDC is used so GitHub Actions can exchange a short-lived GitHub identity token for an AWS role session. No permanent AWS access keys should be stored in GitHub variables, GitHub secrets, workflow files, Amplify, ECS, Docker images, or `.env` files.

The template defines two separate deployment roles:

- `fyp-github-backend-deployer` for the ECS backend image and service update path.
- `fyp-github-agentcore-deployer` for the AgentCore runtime image and runtime update path.

Both roles trust only the GitHub OIDC provider and require this exact subject:

```text
repo:shaheertariq0111/fyp:environment:staging
```

The audience is:

```text
sts.amazonaws.com
```

The GitHub `staging` environment restricts deployments to the `deployment` branch and requires reviewer approval. Because the trust policy uses an environment subject, the branch restriction lives in the GitHub environment rules, not in the AWS OIDC `sub` claim.

### Existing Provider Check

AWS accounts can contain only one IAM OIDC provider for `https://token.actions.githubusercontent.com`. Before deploying the Phase 13 template, check whether the provider already exists:

```powershell
aws iam list-open-id-connect-providers
aws iam get-open-id-connect-provider `
  --open-id-connect-provider-arn arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
```

The template is intentionally conditional:

- `CreateGitHubOidcProvider=false` reuses `ExistingGitHubOidcProviderArn`.
- `CreateGitHubOidcProvider=true` creates `AWS::IAM::OIDCProvider`.

Use `CreateGitHubOidcProvider=true` only when the account does not already have the GitHub Actions provider. This avoids failed stack creation from trying to create a duplicate provider.

### Current Dev Defaults

The current dev defaults in `infra/phase13-github-oidc.yaml` target:

- AWS account: `352306494518`
- Region: `us-east-1`
- Backend ECR repository: `arn:aws:ecr:us-east-1:352306494518:repository/fyp-dev-backend`
- Agent runtime ECR repository: `arn:aws:ecr:us-east-1:352306494518:repository/fyp-dev-agent-runtime`
- ECS cluster/service: `fyp-dev-backend`
- ECS execution role: `arn:aws:iam::352306494518:role/fyp-dev-ecs-execution`
- ECS task role: `arn:aws:iam::352306494518:role/fyp-dev-ecs-app`
- Voice-worker service: `fyp-dev-whatsapp-voice-worker`
- Voice-worker execution role: `arn:aws:iam::352306494518:role/fyp-dev-voice-worker-execution`
- Voice-worker task role: `arn:aws:iam::352306494518:role/fyp-dev-voice-worker-app`
- AgentCore runtime: `arn:aws:bedrock-agentcore:us-east-1:352306494518:runtime/fyp_dev_restaurant_agent-dwLwVnClBF`
- AgentCore execution role: `arn:aws:iam::352306494518:role/fyp-dev-agentcore-execution`

The AgentCore execution role ARN is derived from `infra/phase7-ecs-api.yaml`, where `AgentCoreExecutionRole` uses `RoleName: !Sub ${ProjectName}-agentcore-execution`, and the current dev parameters use `ProjectName=fyp-dev`.

IAM resource-scoping limitations are documented in the template through intentionally small wildcard-resource statements:

- `ecr:GetAuthorizationToken` requires `Resource: "*"`.
- `sts:GetCallerIdentity` uses `Resource: "*"`.
- `ecs:DescribeTaskDefinition` requires `Resource: "*"` because ECS does not support resource-level scoping for that action.
- `ecs:RegisterTaskDefinition` requires `Resource: "*"` because ECS task-definition registration does not support resource-level scoping for that action.
- `ecs:ListTasks` uses `Resource: "*"` and is restricted with `ecs:cluster`; ECS does not support service-resource scoping for that list call. The service itself is still resource-scoped for `ecs:DescribeServices` and `ecs:UpdateService`.

### Local Template Validation

This does not create AWS resources:

```powershell
python -m pip install cfn-lint
cfn-lint `
  infra/phase3-network.yaml `
  infra/phase7-ecs-api.yaml `
  infra/phase11-monitoring.yaml `
  infra/phase13-github-oidc.yaml
```

AWS-authenticated CloudFormation validation is kept out of CI because `validate-infra.yml` is local-only:

```powershell
aws cloudformation validate-template `
  --region us-east-1 `
  --template-body file://infra/phase13-github-oidc.yaml
```

Do not run `aws cloudformation deploy` from CI. Infrastructure stack updates remain a manual operation unless the user explicitly asks for a deployment workflow phase.

### GitHub Variables

Deployment workflows use GitHub environment or repository variables for:

```text
AWS_REGION=us-east-1
BACKEND_DEPLOY_ROLE_ARN=<BackendDeploymentRoleArn output>
AGENTCORE_DEPLOY_ROLE_ARN=<AgentCoreDeploymentRoleArn output>
VOICE_WORKER_DEPLOY_ENABLED=<optional; exact true enables worker task-definition deployment>
```

No GitHub secret should contain AWS access keys.

Before enabling worker deployment integration, manually update the Phase 13 OIDC
stack from `infra/phase13-github-oidc.yaml`. The existing backend deployer remains
scoped to the same cluster and ECR repository, the exact web and worker service ARNs,
and the four exact web/worker execution and task roles. The OIDC trust policy and
`staging` environment subject are unchanged. CloudFormation is never deployed by
the workflow. The worker parameters are `VoiceWorkerEcsServiceName` (default
`fyp-dev-whatsapp-voice-worker`), `VoiceWorkerExecutionRoleArn` (default role
`fyp-dev-voice-worker-execution`), and `VoiceWorkerTaskRoleArn` (default role
`fyp-dev-voice-worker-app`).

## Backend Deployment Rollback

The backend workflow records the previous and new task definition ARNs and automatic
rollback results in the GitHub step summary. If automatic rollback fails or a prior
successful deployment later needs manual rollback, run:

```powershell
aws ecs update-service `
  --region us-east-1 `
  --cluster fyp-dev-backend `
  --service fyp-dev-backend `
  --task-definition <previous-task-definition-arn>

aws ecs wait services-stable `
  --region us-east-1 `
  --cluster fyp-dev-backend `
  --services fyp-dev-backend

Invoke-RestMethod https://vogwq90ly8.execute-api.us-east-1.amazonaws.com/health
```

Replace `<previous-task-definition-arn>` with the ARN shown in the failed or prior successful workflow summary. This updates the existing service back to a previously registered task-definition revision; it does not rebuild images or change infrastructure.

## AgentCore Runtime Rollback

The AgentCore deployment workflow records the previous image URI and previous runtime version in the GitHub step summary. Manual rollback creates another immutable runtime version that points back to the previous known-good image. It does not delete the failed version.

Rollback process:

1. Identify the previous known-good container URI from the workflow summary.
2. Read the current runtime configuration:

```powershell
aws bedrock-agentcore-control get-agent-runtime `
  --region us-east-1 `
  --agent-runtime-id fyp_dev_restaurant_agent-dwLwVnClBF `
  --output json > current-agent-runtime.json
```

3. Build a rollback update input while preserving the current role, network configuration, and mutable settings. Manual rollback may be constructed from a runtime in `READY` or `UPDATE_FAILED`. The previous known-good image may have either a SHA tag or an older manual tag such as `dev-*`, but it must belong to the exact AgentCore runtime ECR repository. Do not attempt rollback while the runtime is actively `CREATING`, `UPDATING`, or `DELETING`.

```powershell
python .github/scripts/build-agentcore-update-input.py `
  --current-runtime current-agent-runtime.json `
  --image-uri <previous-known-good-container-uri> `
  --agent-runtime-id fyp_dev_restaurant_agent-dwLwVnClBF `
  --expected-runtime-arn arn:aws:bedrock-agentcore:us-east-1:352306494518:runtime/fyp_dev_restaurant_agent-dwLwVnClBF `
  --expected-role-arn arn:aws:iam::352306494518:role/fyp-dev-agentcore-execution `
  --expected-ecr-repository 352306494518.dkr.ecr.us-east-1.amazonaws.com/fyp-dev-agent-runtime `
  --mode rollback `
  --client-token agentcore-rollback-<unique-33-plus-character-token> `
  --output agentcore-rollback-input.json
```

4. Update the runtime:

```powershell
aws bedrock-agentcore-control update-agent-runtime `
  --region us-east-1 `
  --cli-input-json file://agentcore-rollback-input.json
```

5. Poll `get-agent-runtime` until status is `READY`.
6. Verify the new rollback runtime version uses the previous image URI and that role/network configuration stayed unchanged.
7. Use the same safe verification policy as the deployment workflow: READY, version, image, role, and network verification unless a future non-mutating invocation mode is added.

The DEFAULT endpoint moves to the latest runtime version automatically. If custom endpoints are introduced later, they require explicit version updates. Existing sessions can continue using the code version with which their microVM started.

## Disabled WhatsApp Voice Rollout

Keep the controls separated and use this order:

1. Stage 1: merge/deploy backend code while voice remains disabled. Existing web
   deployment must work while worker deployment integration is skipped.
2. Stage 2: manually update Phase 7 with `WorkerDesiredCount=0` and
   `WhatsAppVoiceEnabled=false`; verify the jobs table, worker service, roles, and log
   group exist. The worker remains inactive.
3. Stage 3: manually update Phase 13 OIDC, then set the non-secret GitHub `staging`
   Environment variable `VOICE_WORKER_DEPLOY_ENABLED=true`. The workflow deploys the
   same image to both task definitions; worker desired count remains `0`.
4. Stage 4: manually update Phase 11 with
   `WhatsAppVoiceMonitoringEnabled=true`; keep the minimum worker running task count
   at `0`.
5. Stage 5 is later work: confirm the exact Agentflo media hostname and
   authentication, select fixed-language or identify-language transcription mode,
   deploy verified provider configuration, raise worker desired count under
   observation, only then enable inbound enqueueing, perform live verification, and
   remove temporary payload-shape logging in a separate cleanup PR.

Pass B2 performs none of these deployment or activation stages.

## Operational Checklist

Before final synchronization from `deployment` to `main`, confirm:

- backend deployment workflow is green
- AgentCore deployment workflow is green
- Amplify latest job is successful
- ECS desired count equals running count
- public `/health` succeeds
- AgentCore status is `READY`
- monitoring stack is healthy
- `main` and `deployment` are synchronized after final merge

## Future Deployment Phases

When a deployment job uses the GitHub environment named `staging`, the AWS OIDC trust subject must be:

```text
repo:shaheertariq0111/fyp:environment:staging
```

The GitHub `staging` environment restricts deployments to the `deployment` branch and requires reviewer approval. Do not use branch-based trust and environment-based trust interchangeably; choose the subject that matches the workflow's environment usage.

## Local Commands Matching CI

Backend:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
..\.venv\Scripts\python.exe -m pytest
cd ..
docker build --platform linux/amd64 -t fyp-backend-ci:local backend
docker run --rm -d --name fyp-backend-ci-health -p 8000:8000 `
  -e ENVIRONMENT=test `
  -e AWS_REGION=us-east-1 `
  -e MENU_TABLE_NAME=ci-menu `
  -e CARTS_TABLE_NAME=ci-carts `
  -e ORDERS_TABLE_NAME=ci-orders `
  -e CUSTOMERS_TABLE_NAME=ci-customers `
  -e AGENT_SESSIONS_TABLE_NAME=ci-agent-sessions `
  -e AGENT_REQUESTS_TABLE_NAME=ci-agent-requests `
  -e MENU_SESSIONS_TABLE_NAME=ci-menu-sessions `
  -e AUDIT_TABLE_NAME=ci-audit `
  -e MENU_SITE_BASE_URL=http://localhost:3000/menu `
  -e SESSION_TOKEN_SECRET=ci-session-token-secret `
  -e RESTAURANT_ID=ci-restaurant `
  -e BRANCH_ID=default `
  fyp-backend-ci:local
Invoke-RestMethod http://localhost:8000/health
docker rm -f fyp-backend-ci-health
```

Agent runtime:

```powershell
cd agent-runtime
$env:PYTHONPATH='E:\fyp-agent\agent-runtime\src;E:\fyp-agent\backend'
E:\fyp-agent\.venv\Scripts\python.exe -m pytest tests
cd ..
.\.venv\Scripts\python.exe -m pytest .github\scripts\tests
docker build --platform linux/arm64 -f agent-runtime/Dockerfile -t fyp-agent-runtime-ci:local .
docker run --rm -d --platform linux/arm64 --name fyp-agent-runtime-ci-ping -p 8080:8080 fyp-agent-runtime-ci:local
Invoke-RestMethod http://localhost:8080/ping
docker rm -f fyp-agent-runtime-ci-ping
```

Frontend:

```powershell
cd frontend
npm ci
npm run typecheck
npm run lint
npm run build
```

Infrastructure:

```powershell
python -m pip install cfn-lint
cfn-lint infra/phase3-network.yaml infra/phase7-ecs-api.yaml infra/phase11-monitoring.yaml infra/phase13-github-oidc.yaml
```

Authenticated CloudFormation validation is deferred to a later phase:

```powershell
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase3-network.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase7-ecs-api.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase11-monitoring.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase13-github-oidc.yaml
```

Do not run those AWS commands in CI from `validate-infra.yml`; that workflow intentionally remains local validation only.
