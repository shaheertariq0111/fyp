from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "infra" / "phase7-ecs-api.yaml"
PARAMETERS_PATH = ROOT / "infra" / "parameters" / "dev.example.json"


class CloudFormationLoader(yaml.SafeLoader):
    pass


def _cloudformation_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = None
    key = "Ref" if tag_suffix == "Ref" else f"Fn::{tag_suffix}"
    return {key: value}


CloudFormationLoader.add_multi_constructor("!", _cloudformation_tag)


def template():
    return yaml.load(
        TEMPLATE_PATH.read_text(encoding="utf-8"),
        Loader=CloudFormationLoader,
    )


def parameter_map():
    return {
        entry["ParameterKey"]: entry["ParameterValue"]
        for entry in json.loads(PARAMETERS_PATH.read_text(encoding="utf-8"))
    }


def statements(resource):
    return resource["Properties"]["PolicyDocument"]["Statement"]


def statement_by_sid(resource, sid):
    return next(item for item in statements(resource) if item.get("Sid") == sid)


def container_environment(data, logical_id):
    container = data["Resources"][logical_id]["Properties"][
        "ContainerDefinitions"
    ][0]
    return {item["Name"]: item["Value"] for item in container["Environment"]}


def test_receipt_parameters_are_safe_and_independently_disabled():
    data = template()
    parameters = data["Parameters"]

    assert parameters["ReceiptActivationEnabled"] == {
        "Type": "String",
        "Default": "false",
        "AllowedValues": ["true", "false"],
        "Description": (
            "Enables creation of new receipt jobs only; processing and "
            "recovery are controlled independently."
        ),
    }
    assert parameters["ReceiptJobsTableName"]["Default"] == "fyp-dev-ReceiptJobs"
    assert parameters["ReceiptJobTtlHours"] == {
        "Type": "Number",
        "Default": 336,
        "MinValue": 1,
        "MaxValue": 336,
    }
    assert parameters["ReceiptEnqueueRetrySeconds"] == {
        "Type": "Number",
        "Default": 60,
        "MinValue": 1,
        "MaxValue": 3600,
    }
    assert parameters["ReceiptLambdaImageUri"]["Default"] == ""
    assert "initial infrastructure bootstrap" in parameters[
        "ReceiptLambdaImageUri"
    ]["Description"]
    for name in ("ReceiptProcessingEnabled", "ReceiptRecoveryEnabled"):
        assert parameters[name]["Default"] == "false"
        assert parameters[name]["AllowedValues"] == ["true", "false"]
    assert parameters["ReceiptMerchantName"]["Default"] == "Restaurant"
    assert parameters["ReceiptWorkerLeaseSeconds"] == {
        "Type": "Number",
        "Default": 120,
        "MinValue": 61,
        "MaxValue": 3600,
    }

    conditions = data["Conditions"]
    assert conditions["IsReceiptActivationEnabled"] == {
        "Fn::Equals": [{"Ref": "ReceiptActivationEnabled"}, "true"]
    }
    processing = json.dumps(conditions["ShouldCreateReceiptProcessingEventSource"])
    recovery = json.dumps(conditions["ShouldCreateReceiptRecoverySchedule"])
    assert "IsReceiptProcessingEnabled" in processing
    assert "IsReceiptRecoveryEnabled" in recovery
    assert "ReceiptActivation" not in processing
    assert "ReceiptActivation" not in recovery


def test_receipt_jobs_table_matches_durable_outbox_schema_and_retention():
    resource = template()["Resources"]["ReceiptJobsTable"]
    properties = resource["Properties"]

    assert resource["Type"] == "AWS::DynamoDB::Table"
    assert resource["DeletionPolicy"] == "Retain"
    assert resource["UpdateReplacePolicy"] == "Retain"
    assert properties["TableName"] == {"Ref": "ReceiptJobsTableName"}
    assert properties["BillingMode"] == "PAY_PER_REQUEST"
    assert properties["AttributeDefinitions"] == [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "N"},
    ]
    assert properties["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert properties["GlobalSecondaryIndexes"] == [{
        "IndexName": "DueJobsIndex",
        "KeySchema": [
            {"AttributeName": "GSI1PK", "KeyType": "HASH"},
            {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    }]
    assert properties["TimeToLiveSpecification"] == {
        "AttributeName": "expires_at",
        "Enabled": True,
    }
    assert properties["PointInTimeRecoverySpecification"] == {
        "PointInTimeRecoveryEnabled": True
    }
    assert properties["SSESpecification"] == {"SSEEnabled": True}


def test_receipt_bucket_is_private_encrypted_and_short_lived():
    data = template()
    bucket = data["Resources"]["ReceiptBucket"]
    properties = bucket["Properties"]

    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["UpdateReplacePolicy"] == "Retain"
    assert properties["BucketName"] == {
        "Fn::Sub": "${ProjectName}-r-${AWS::AccountId}-${AWS::Region}"
    }
    assert properties["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert properties["OwnershipControls"] == {
        "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
    }
    assert properties["BucketEncryption"] == {
        "ServerSideEncryptionConfiguration": [{
            "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}
        }]
    }
    assert properties["LifecycleConfiguration"] == {"Rules": [{
        "Id": "ExpireReceiptArtifacts",
        "Status": "Enabled",
        "Prefix": "receipts/",
        "ExpirationInDays": 30,
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
    }]}
    for forbidden in ("WebsiteConfiguration", "CorsConfiguration", "AccessControl"):
        assert forbidden not in properties

    policy = data["Resources"]["ReceiptBucketPolicy"]
    assert policy["DeletionPolicy"] == "Retain"
    assert policy["UpdateReplacePolicy"] == "Retain"
    assert statements(policy) == [{
        "Sid": "DenyInsecureTransport",
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:*",
        "Resource": [
            {"Fn::GetAtt": "ReceiptBucket.Arn"},
            {"Fn::Sub": "${ReceiptBucket.Arn}/*"},
        ],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
    }]


def test_receipt_queues_are_encrypted_redriven_and_timeout_safe():
    resources = template()["Resources"]
    queue = resources["ReceiptProcessingQueue"]["Properties"]
    dlq = resources["ReceiptProcessingDeadLetterQueue"]["Properties"]
    function = resources["ReceiptProcessingFunction"]["Properties"]

    assert queue["SqsManagedSseEnabled"] is True
    assert dlq["SqsManagedSseEnabled"] is True
    assert dlq["MessageRetentionPeriod"] == 1209600
    assert queue["RedrivePolicy"] == {
        "deadLetterTargetArn": {
            "Fn::GetAtt": "ReceiptProcessingDeadLetterQueue.Arn"
        },
        "maxReceiveCount": 3,
    }
    assert queue["VisibilityTimeout"] == 360
    assert queue["VisibilityTimeout"] >= 6 * function["Timeout"]
    assert not any(
        resource.get("Type") == "AWS::SQS::QueuePolicy"
        for resource in resources.values()
    )


def test_receipt_ecr_repository_explicitly_allows_only_lambda_image_reads():
    repository = template()["Resources"]["ReceiptLambdaRepository"]
    policy = repository["Properties"]["RepositoryPolicyText"]

    assert policy == {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowReceiptLambdaImageRetrieval",
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": [
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            ],
        }],
    }
    serialized = json.dumps(policy)
    assert '"Principal": "*"' not in serialized
    assert "ecr:*" not in serialized
    for forbidden in (
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:SetRepositoryPolicy",
        "ecr:DeleteRepository",
    ):
        assert forbidden not in serialized


def test_receipt_bucket_name_is_safe_at_maximum_project_name_length():
    data = template()
    expression = data["Resources"]["ReceiptBucket"]["Properties"]["BucketName"]

    assert data["Parameters"]["ProjectName"]["AllowedPattern"] == (
        "^[a-z][a-z0-9-]{1,31}$"
    )
    assert expression == {
        "Fn::Sub": "${ProjectName}-r-${AWS::AccountId}-${AWS::Region}"
    }
    maximum_project_name = 32
    account_id = 12
    longest_current_region_name = 15
    separators_and_discriminator = len("-r-") + len("-")
    maximum_bucket_name = (
        maximum_project_name
        + separators_and_discriminator
        + account_id
        + longest_current_region_name
    )
    assert maximum_bucket_name == 63
    assert maximum_bucket_name <= 63


def test_blank_image_bootstraps_base_resources_without_lambda_instantiation():
    data = template()
    resources = data["Resources"]

    assert data["Parameters"]["ReceiptLambdaImageUri"]["Default"] == ""
    assert data["Conditions"]["HasReceiptLambdaImageUri"] == {
        "Fn::Not": [{"Fn::Equals": [{"Ref": "ReceiptLambdaImageUri"}, ""]}]
    }
    assert resources["ReceiptProcessingFunction"]["Condition"] == (
        "ShouldCreateReceiptProcessingLambda"
    )
    assert resources["ReceiptRecoveryFunction"]["Condition"] == (
        "HasReceiptLambdaImageUri"
    )
    assert resources["ReceiptProcessingEventSource"]["Condition"] == (
        "ShouldCreateReceiptProcessingEventSource"
    )
    assert resources["ReceiptRecoverySchedule"]["Condition"] == (
        "ShouldCreateReceiptRecoverySchedule"
    )
    for logical_id in (
        "ReceiptLambdaRepository",
        "ReceiptJobsTable",
        "ReceiptProcessingQueue",
        "ReceiptProcessingDeadLetterQueue",
        "ReceiptBucket",
        "ReceiptProcessingLambdaRole",
        "ReceiptRecoveryLambdaRole",
        "ReceiptProcessingLogGroup",
        "ReceiptRecoveryLogGroup",
    ):
        assert "Condition" not in resources[logical_id]


def test_processing_lambda_has_exact_isolated_environment_and_handler():
    resource = template()["Resources"]["ReceiptProcessingFunction"]
    properties = resource["Properties"]
    variables = properties["Environment"]["Variables"]

    assert properties["PackageType"] == "Image"
    assert properties["Code"] == {"ImageUri": {"Ref": "ReceiptLambdaImageUri"}}
    assert properties["Architectures"] == ["x86_64"]
    assert properties["MemorySize"] == 512
    assert properties["Timeout"] == 60
    assert "ImageConfig" not in properties
    assert set(variables) == {
        "ORDERS_TABLE_NAME",
        "RECEIPT_JOBS_TABLE_NAME",
        "RECEIPT_JOB_QUEUE_URL",
        "RECEIPT_JOB_TTL_HOURS",
        "RECEIPT_ENQUEUE_RETRY_SECONDS",
        "RECEIPT_BUCKET_NAME",
        "RECEIPT_MERCHANT_NAME",
        "RECEIPT_WORKER_LEASE_SECONDS",
        "AGENTFLO_GATEWAY_BASE_URL",
        "AGENTFLO_GATEWAY_TENANT_ID",
        "AGENTFLO_GATEWAY_AGENT_ID",
        "AGENTFLO_GATEWAY_ACTOR_ID",
        "AGENTFLO_GATEWAY_API_KEY_SECRET_ARN",
    }
    assert "AGENTFLO_GATEWAY_API_KEY" not in variables


def test_recovery_lambda_has_only_outbox_environment_and_recovery_handler():
    properties = template()["Resources"]["ReceiptRecoveryFunction"]["Properties"]

    assert properties["ImageConfig"] == {
        "Command": ["src.lambda_handlers.receipt_outbox_recovery.handler"]
    }
    assert set(properties["Environment"]["Variables"]) == {
        "RECEIPT_JOBS_TABLE_NAME",
        "RECEIPT_JOB_QUEUE_URL",
        "RECEIPT_JOB_TTL_HOURS",
        "RECEIPT_ENQUEUE_RETRY_SECONDS",
    }
    serialized = json.dumps(properties)
    for forbidden in (
        "ORDERS_TABLE_NAME",
        "RECEIPT_BUCKET_NAME",
        "RECEIPT_MERCHANT_NAME",
        "AGENTFLO_",
        "SECRET",
    ):
        assert forbidden not in serialized


def test_processing_lambda_iam_is_exact_and_least_privilege():
    data = template()
    policy = data["Resources"]["ReceiptProcessingLambdaPolicy"]
    secret_policy = data["Resources"]["ReceiptProcessingLambdaSecretPolicy"]

    assert statement_by_sid(policy, "ProcessReceiptJobState")["Action"] == [
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    ]
    assert statement_by_sid(policy, "QueryOrderReceiptSnapshot") == {
        "Sid": "QueryOrderReceiptSnapshot",
        "Effect": "Allow",
        "Action": ["dynamodb:Query"],
        "Resource": {
            "Fn::Sub": (
                "arn:${AWS::Partition}:dynamodb:${AWS::Region}:"
                "${AWS::AccountId}:table/${OrdersTableName}/index/GSI1"
            )
        },
    }
    assert statement_by_sid(policy, "ManageReceiptArtifacts")["Resource"] == {
        "Fn::Sub": "${ReceiptBucket.Arn}/receipts/*"
    }
    assert set(statement_by_sid(policy, "ConsumeReceiptQueue")["Action"]) == {
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
    }
    secret = statement_by_sid(secret_policy, "ReadAgentfloGatewayApiKey")
    assert secret["Action"] == ["secretsmanager:GetSecretValue"]
    assert secret["Resource"] == {"Ref": "AgentfloGatewayApiKeySecretArn"}
    serialized = json.dumps([policy, secret_policy])
    assert "sqs:SendMessage" not in serialized
    assert "dynamodb:Scan" not in serialized
    assert "dynamodb:*" not in serialized


def test_recovery_lambda_iam_is_outbox_only():
    policy = template()["Resources"]["ReceiptRecoveryLambdaPolicy"]

    assert statement_by_sid(policy, "RecoverReceiptJobState")["Action"] == [
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
    ]
    assert statement_by_sid(policy, "QueryReceiptOutbox") == {
        "Sid": "QueryReceiptOutbox",
        "Effect": "Allow",
        "Action": ["dynamodb:Query"],
        "Resource": {"Fn::Sub": "${ReceiptJobsTable.Arn}/index/DueJobsIndex"},
    }
    assert statement_by_sid(policy, "ResubmitReceiptQueueMessage")["Action"] == [
        "sqs:SendMessage"
    ]
    serialized = json.dumps(policy)
    for forbidden in (
        "OrdersTable",
        "ReceiptBucket",
        "secretsmanager",
        "dynamodb:Scan",
        "AGENTFLO",
    ):
        assert forbidden not in serialized


def test_processing_event_source_and_recovery_schedule_are_separately_gated():
    data = template()
    resources = data["Resources"]
    event_source = resources["ReceiptProcessingEventSource"]
    schedule = resources["ReceiptRecoverySchedule"]
    permission = resources["ReceiptRecoverySchedulePermission"]

    assert event_source["Properties"] == {
        "EventSourceArn": {"Fn::GetAtt": "ReceiptProcessingQueue.Arn"},
        "FunctionName": {"Ref": "ReceiptProcessingFunction"},
        "BatchSize": 5,
        "MaximumBatchingWindowInSeconds": 0,
        "FunctionResponseTypes": ["ReportBatchItemFailures"],
    }
    assert schedule["Properties"]["ScheduleExpression"] == "rate(1 minute)"
    assert schedule["Properties"]["State"] == "ENABLED"
    assert schedule["Properties"]["Targets"] == [{
        "Arn": {"Fn::GetAtt": "ReceiptRecoveryFunction.Arn"},
        "Id": "ReceiptRecoveryLambda",
    }]
    assert permission["Properties"]["Principal"] == "events.amazonaws.com"
    assert permission["Properties"]["SourceArn"] == {
        "Fn::GetAtt": "ReceiptRecoverySchedule.Arn"
    }
    serialized_conditions = json.dumps(data["Conditions"])
    assert "ReceiptActivationEnabled" not in json.dumps(
        data["Conditions"]["ShouldCreateReceiptProcessingEventSource"]
    )
    assert "ReceiptActivationEnabled" not in json.dumps(
        data["Conditions"]["ShouldCreateReceiptRecoverySchedule"]
    )
    assert "ReceiptProcessingEnabled" in serialized_conditions
    assert "ReceiptRecoveryEnabled" in serialized_conditions


def test_ecs_receipt_environment_is_submission_only_for_backend_and_voice():
    data = template()
    expected = {
        "RECEIPT_ACTIVATION_ENABLED": {"Ref": "ReceiptActivationEnabled"},
        "RECEIPT_JOBS_TABLE_NAME": {"Ref": "ReceiptJobsTableName"},
        "RECEIPT_JOB_QUEUE_URL": {"Ref": "ReceiptProcessingQueue"},
        "RECEIPT_JOB_TTL_HOURS": {"Ref": "ReceiptJobTtlHours"},
        "RECEIPT_ENQUEUE_RETRY_SECONDS": {
            "Ref": "ReceiptEnqueueRetrySeconds"
        },
    }
    for logical_id in (
        "BackendTaskDefinition",
        "WhatsAppVoiceWorkerTaskDefinition",
    ):
        environment = container_environment(data, logical_id)
        assert {name: environment[name] for name in expected} == expected
        for forbidden in (
            "RECEIPT_BUCKET_NAME",
            "RECEIPT_MERCHANT_NAME",
            "RECEIPT_WORKER_LEASE_SECONDS",
            "AGENTFLO_GATEWAY_API_KEY_SECRET_ARN",
        ):
            assert forbidden not in environment


def test_ecs_receipt_submission_iam_is_conditional_and_narrow():
    data = template()
    for logical_id, role in (
        ("EcsTaskReceiptSubmissionPolicy", "EcsTaskRole"),
        (
            "WhatsAppVoiceWorkerReceiptSubmissionPolicy",
            "WhatsAppVoiceWorkerTaskRole",
        ),
    ):
        policy = data["Resources"][logical_id]
        assert policy["Condition"] == "IsReceiptActivationEnabled"
        assert policy["Properties"]["Roles"] == [{"Ref": role}]
        assert statement_by_sid(policy, "SubmitReceiptJobState")["Action"] == [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
        ]
        assert statement_by_sid(policy, "SubmitReceiptQueueMessage")["Action"] == [
            "sqs:SendMessage"
        ]
        serialized = json.dumps(policy)
        for forbidden in (
            "s3:",
            "secretsmanager:",
            "lambda:",
            "dynamodb:Query",
            "dynamodb:Scan",
        ):
            assert forbidden not in serialized


def test_receipt_outputs_are_useful_and_non_sensitive():
    outputs = template()["Outputs"]
    assert outputs["ReceiptLambdaRepositoryUri"]["Value"] == {
        "Fn::GetAtt": "ReceiptLambdaRepository.RepositoryUri"
    }
    assert outputs["ReceiptJobsTableName"]["Value"] == {
        "Ref": "ReceiptJobsTable"
    }
    assert outputs["ReceiptProcessingQueueUrl"]["Value"] == {
        "Ref": "ReceiptProcessingQueue"
    }
    assert outputs["ReceiptProcessingQueueArn"]["Value"] == {
        "Fn::GetAtt": "ReceiptProcessingQueue.Arn"
    }
    assert outputs["ReceiptProcessingDeadLetterQueueUrl"]["Value"] == {
        "Ref": "ReceiptProcessingDeadLetterQueue"
    }
    assert outputs["ReceiptBucketName"]["Value"] == {"Ref": "ReceiptBucket"}
    serialized = json.dumps(outputs)
    assert "ApiKey" not in serialized
    assert "SecretArn" not in serialized


def test_dev_example_uses_safe_disabled_receipt_defaults():
    parameters = parameter_map()
    assert {
        name: parameters[name]
        for name in (
            "ReceiptActivationEnabled",
            "ReceiptJobsTableName",
            "ReceiptJobTtlHours",
            "ReceiptEnqueueRetrySeconds",
            "ReceiptLambdaImageUri",
            "ReceiptProcessingEnabled",
            "ReceiptRecoveryEnabled",
            "ReceiptMerchantName",
            "ReceiptWorkerLeaseSeconds",
        )
    } == {
        "ReceiptActivationEnabled": "false",
        "ReceiptJobsTableName": "fyp-dev-ReceiptJobs",
        "ReceiptJobTtlHours": "336",
        "ReceiptEnqueueRetrySeconds": "60",
        "ReceiptLambdaImageUri": "",
        "ReceiptProcessingEnabled": "false",
        "ReceiptRecoveryEnabled": "false",
        "ReceiptMerchantName": "Restaurant",
        "ReceiptWorkerLeaseSeconds": "120",
    }
