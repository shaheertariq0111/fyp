You are working on my final-year project repository. Inspect the entire repository before changing anything.

The project currently contains:

* A Next.js frontend that has already been downgraded to Next.js 15
* A FastAPI backend
* A Strands-based restaurant ordering agent
* Amazon Bedrock model integration
* DynamoDB-based menu, cart, order, customer and session functionality
* Customer chat, menu and admin dashboard features

I want to prepare and deploy the project using the following AWS architecture.

## Target architecture

### Frontend

Deploy the Next.js 15 frontend using AWS Amplify Hosting.

Amplify must provide:

* GitHub-based deployment
* Automatic HTTPS
* AWS-generated `amplifyapp.com` URL
* Environment variable configuration
* Automatic rebuilds after Git pushes

We do not own a custom domain and will not purchase one.

### Public API

Use Amazon API Gateway HTTP API as the public HTTPS endpoint.

Do not use an Application Load Balancer.

The API Gateway endpoint should look similar to:

```text
https://xxxxxxxx.execute-api.us-east-1.amazonaws.com
```

API Gateway must connect privately to the ECS backend using:

```text
API Gateway HTTP API
    ↓
VPC Link
    ↓
AWS Cloud Map service discovery
    ↓
ECS Fargate FastAPI backend
```

### Application backend

Deploy the normal FastAPI application backend using:

```text
Docker
    ↓
Amazon ECR
    ↓
ECS Fargate
```

The ECS backend remains responsible for:

* REST API routes
* Admin authentication
* Menu APIs
* Cart APIs
* Order APIs
* Customer APIs
* Session validation
* DynamoDB business logic
* Request status APIs
* Calling Amazon Bedrock AgentCore Runtime
* Returning agent results to the frontend
* CloudWatch application logging

Do not deploy the Strands reasoning agent inside the normal ECS backend in the final architecture.

### AI agent

Move the Strands agent to Amazon Bedrock AgentCore Runtime.

AgentCore Runtime should be responsible for:

* Running the Strands agent
* Natural-language reasoning
* Tool selection
* Bedrock model invocation
* Agent orchestration
* Conversation processing

Use AgentCore Memory for short-term conversation memory.

Use:

```text
actor_id = verified customer or user ID
session_id = current conversation ID
```

Do not implement long-term customer profiling yet.

The intended flow is:

```text
Amplify frontend
    ↓
API Gateway HTTP API
    ↓
VPC Link
    ↓
Cloud Map
    ↓
ECS FastAPI backend
    ↓
Amazon Bedrock AgentCore Runtime
    ↓
Strands agent
    ├── Amazon Bedrock model
    ├── AgentCore Memory
    ├── DynamoDB tools
    └── Bedrock Knowledge Base later
```

## Critical API Gateway timeout requirement

API Gateway HTTP APIs have a limited integration timeout. The current synchronous agent request flow must not depend on a long-running HTTP request.

Refactor the chat flow into asynchronous request processing.

Expected design:

```text
Frontend sends chat message
    ↓
POST /api/chat
    ↓
Backend validates request
    ↓
Backend creates request/job record
    ↓
Backend starts AgentCore processing
    ↓
Backend immediately returns request_id
```

Example immediate response:

```json
{
  "request_id": "req-123",
  "status": "processing"
}
```

The frontend then polls:

```text
GET /api/chat/req-123
```

Possible processing response:

```json
{
  "request_id": "req-123",
  "status": "processing"
}
```

Completed response:

```json
{
  "request_id": "req-123",
  "status": "completed",
  "response": "Your order has been confirmed."
}
```

Failed response:

```json
{
  "request_id": "req-123",
  "status": "failed",
  "error_code": "AGENT_INVOCATION_FAILED",
  "message": "The request could not be completed."
}
```

Use DynamoDB to persist agent request status.

Create or reuse a suitable table such as:

```text
AgentRequests
```

Recommended fields:

```text
request_id
actor_id
session_id
status
message
response
error_code
created_at
updated_at
expires_at
```

Use TTL on `expires_at`.

Do not keep request status only in ECS memory because ECS tasks can restart.

## Future WhatsApp compatibility

Design the asynchronous agent-processing architecture so it can later support:

```text
Meta WhatsApp webhook
    ↓
API Gateway
    ↓
Lambda
    ↓
SQS
    ↓
AgentCore Runtime
```

Do not implement WhatsApp, Lambda or SQS now unless reusable interfaces are required.

Keep the current web chat working during this deployment phase.

## Required work

Perform the work in phases.

### Phase 1: Repository inspection

Before making changes:

1. Inspect the complete frontend and backend structure.
2. Identify:

   * FastAPI entrypoint
   * Existing API routes
   * Current chat request and response flow
   * Strands agent entrypoint
   * Agent tools
   * DynamoDB service layer
   * Current session implementation
   * Current environment variables
   * Existing Docker configuration
   * Existing deployment files
   * Frontend API client
   * Admin authentication flow
3. Run the current tests.
4. Run the frontend production build.
5. Document all deployment blockers.

Do not assume file paths. Confirm them from the repository.

### Phase 2: Separate the agent from the application backend

Refactor the code into clearly separated components.

Suggested structure, adjusted to fit the existing repository:

```text
backend/
    src/
        api/
        services/
        repositories/
        agent_client/
        config/

agent-runtime/
    src/
        agent/
        tools/
        memory/
        runtime/
```

Do not duplicate business logic unnecessarily.

Shared schemas may be placed in a shared package if needed.

The ECS backend must call AgentCore through a dedicated client abstraction such as:

```python
class AgentRuntimeClient:
    async def start_request(...):
        ...

    async def get_request_status(...):
        ...
```

Keep AWS-specific calls behind services or adapters. Do not place them directly inside FastAPI route handlers.

### Phase 3: Implement asynchronous agent requests

Implement:

```text
POST /api/chat
GET /api/chat/{request_id}
```

Preserve existing request fields wherever possible so the frontend requires minimal changes.

Requirements:

* Generate a unique request ID
* Validate actor/user and session IDs
* Store the initial request in DynamoDB
* Set status to `processing`
* Invoke AgentCore asynchronously
* Update status to `completed` or `failed`
* Store only required response data
* Add TTL
* Make duplicate submission handling safe where practical
* Add structured error codes
* Do not expose raw AWS exceptions to the frontend

If AgentCore invocation cannot truly run asynchronously directly from the request handler, implement a safe interim design and clearly explain the limitation. Do not fake asynchronous behavior using an in-memory background task that can disappear when ECS restarts.

### Phase 4: Update the frontend

Update the Next.js 15 frontend to:

1. Send the message through `POST /api/chat`.
2. Receive a `request_id`.
3. Poll `GET /api/chat/{request_id}`.
4. Stop polling when:

   * status is `completed`
   * status is `failed`
   * a client-side maximum wait threshold is reached
5. Show clear processing, completed and failed states.
6. Prevent accidental duplicate submission.
7. Preserve the conversation UI.
8. Preserve menu, cart, order and admin functionality.

Use a reasonable polling interval such as 1–2 seconds.

Do not hard-code AWS endpoint URLs.

Use:

```text
NEXT_PUBLIC_API_BASE_URL
NEXT_PUBLIC_BRANCH_ID
```

### Phase 5: Containerize the ECS backend

Create or correct:

```text
backend/Dockerfile
backend/.dockerignore
```

Requirements:

* Linux-compatible image
* Non-root user
* Port 8000
* Uvicorn production entrypoint
* No `.env` copied into the image
* No local AWS credentials copied
* No tests or caches copied unnecessarily
* Health endpoint at `/health`
* Suitable for Fargate `linux/amd64`

Expected entrypoint:

```text
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Test the image locally.

### Phase 6: Prepare AgentCore Runtime deployment

Create the files and configuration required to deploy the Strands agent to Amazon Bedrock AgentCore Runtime.

Requirements:

* Keep the existing Strands agent behavior
* Use the existing Bedrock model unless configuration requires otherwise
* Use environment variables for model and resource IDs
* Integrate AgentCore short-term memory
* Remove reliance on local file-based Strands session storage
* Keep tools explicit and testable
* Use IAM roles instead of AWS access keys
* Add structured CloudWatch-compatible logs
* Document the AgentCore deployment command and prerequisites

Do not place normal FastAPI business APIs inside AgentCore Runtime.

### Phase 7: Prepare ECS, ECR, Cloud Map and API Gateway infrastructure

Use infrastructure as code.

Prefer AWS CDK, AWS SAM or CloudFormation based on what best fits the existing repository. Do not introduce Terraform unless the repository already uses it or there is a strong reason.

Create infrastructure for:

* ECR backend repository
* ECS cluster
* ECS Fargate task definition
* ECS service
* Cloud Map private namespace
* Cloud Map service
* Service registration for ECS tasks
* API Gateway HTTP API
* API Gateway VPC Link
* Private integration from API Gateway to the Cloud Map service
* API routes
* CloudWatch log groups
* Security groups
* Required IAM roles
* DynamoDB AgentRequests table if it does not already exist
* Secrets Manager references

Do not create an Application Load Balancer.

Use one AWS region:

```text
us-east-1
```

For development, use names with a `shaheer-fyp-dev` or `fyp-dev` prefix.

Ensure the ECS backend is not directly publicly exposed.

### Phase 8: IAM security

Create separate IAM roles.

#### ECS task execution role

Allow only what ECS needs to:

* Pull images from ECR
* Send logs to CloudWatch
* Inject specified Secrets Manager values

#### ECS application task role

Allow only what the FastAPI backend needs to:

* Access the required DynamoDB tables
* Invoke AgentCore Runtime
* Read only required secrets
* Write required logs or metrics

#### AgentCore execution role

Allow only what the agent needs to:

* Invoke the configured Bedrock model
* Access AgentCore Memory
* Access approved DynamoDB resources through tools
* Access a Knowledge Base later if configured
* Write logs

Do not use:

```text
AdministratorAccess
AmazonBedrockFullAccess
AmazonDynamoDBFullAccess
```

Use resource-specific permissions.

Do not put AWS access keys in:

* `.env`
* Docker images
* GitHub
* Amplify
* ECS environment variables

### Phase 9: Secrets and configuration

Store sensitive values in AWS Secrets Manager.

Examples:

```text
SESSION_TOKEN_SECRET
ADMIN_PASSWORD
ADMIN_SESSION_SECRET
```

Normal ECS environment variables may include:

```text
ENVIRONMENT
AWS_REGION
BEDROCK_MODEL_ID
AGENTCORE_RUNTIME_ARN
AGENT_REQUESTS_TABLE_NAME
MENU_TABLE_NAME
CARTS_TABLE_NAME
ORDERS_TABLE_NAME
CUSTOMERS_TABLE_NAME
AGENT_SESSIONS_TABLE_NAME
MENU_SESSIONS_TABLE_NAME
AUDIT_TABLE_NAME
RESTAURANT_ID
BRANCH_ID
LOG_LEVEL
FRONTEND_CORS_ORIGINS
```

Amplify environment variables should include:

```text
NEXT_PUBLIC_API_BASE_URL
NEXT_PUBLIC_BRANCH_ID
```

`NEXT_PUBLIC_MENU_BASE_URL` is not required by the current frontend. Generated menu links are owned by the backend through `MENU_SITE_BASE_URL`.

The frontend will use an Amplify-provided URL. We do not have a custom domain.

Configure backend CORS to allow the exact Amplify URL.

Do not use:

```text
FRONTEND_CORS_ORIGINS=*
```

### Phase 10: Prepare Amplify deployment

The frontend already uses Next.js 15.

Create or update the required Amplify build configuration.

The repository may be a monorepo, so correctly configure:

* Frontend root directory
* Build commands
* Output directory
* Node version if required
* Environment variables
* GitHub deployment instructions

Use Amplify’s generated HTTPS URL.

Do not add Route 53, ACM or custom-domain configuration.

### Phase 11: Logging and monitoring

Add structured logs for:

* Request ID
* Actor ID where safe
* Session ID where safe
* Route
* HTTP status
* Response time
* AgentCore invocation
* Agent tool called
* Agent request status
* Bedrock response time
* DynamoDB failures
* Error code

Do not log:

* Passwords
* Access tokens
* Session secrets
* AWS credentials
* Full customer addresses unnecessarily
* Full private conversations unless explicitly required for debugging

Create or document alarms for:

* ECS task failure
* API Gateway 5xx errors
* High API latency
* AgentCore invocation failure
* DynamoDB throttling
* Agent request failure rate

Document the concrete CloudWatch alarm and metric-filter setup without creating the alarms automatically.

### Phase 12: Validation

Run:

* Backend unit tests
* Backend integration tests where available
* Agent tool tests
* Frontend type checking
* Frontend linting
* Frontend production build
* Docker build
* Container health test
* Infrastructure validation or linting

Test these application flows:

* Health check
* Start conversation
* Submit chat message
* Poll request status
* Receive agent response
* View menu
* Search menu
* Add item to cart
* Modify quantity
* Remove item
* Confirm order
* Check order status
* Start new conversation
* Admin login
* Admin dashboard
* ECS task restart without losing request status

## Constraints

* Do not add an Application Load Balancer.
* Do not add a custom domain.
* Do not use Route 53 or ACM.
* Do not deploy the frontend on ECS.
* Do not leave the Strands agent permanently inside the FastAPI ECS container.
* Do not implement long-term AgentCore memory yet.
* Do not implement WhatsApp yet.
* Do not implement WAF or QuickSight yet.
* Do not break existing menu, cart, order or admin functionality.
* Do not commit `.env` files or secrets.
* Do not make destructive AWS changes without showing me the exact command and asking for confirmation.
* Do not create every AWS service at once.
* Work in phases and validate each phase before proceeding.
* Reuse existing repository patterns rather than rewriting the entire project.
* Keep code production-oriented and avoid temporary hacks.

## Required output

After inspecting the repository, provide:

1. Current architecture summary
2. Deployment blockers
3. Exact files that need to change
4. Proposed implementation sequence
5. AWS resources that will be created
6. Estimated recurring AWS cost categories
7. Risks and limitations
8. Tests that will verify each phase

Then begin implementing the code changes.

After each phase, report:

* Files changed
* Why they changed
* Tests run
* Test results
* Remaining blockers

At the end, produce:

```text
DEPLOYMENT_GUIDE.md
ARCHITECTURE.md
ENVIRONMENT_VARIABLES.md
AWS_RESOURCES.md
DEPLOYMENT_CHECKLIST.md
```

Also provide exact deployment commands for:

* Building the backend Docker image
* Logging into ECR
* Pushing the image
* Deploying infrastructure
* Deploying AgentCore Runtime
* Deploying the frontend through Amplify
* Updating an existing deployment
* Rolling back a failed deployment

Start by inspecting the repository. Do not make assumptions about the current implementation.
