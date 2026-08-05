from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.infrastructure.config import Settings
from src.scripts.create_dynamodb_tables import table_definitions
from test_config import BASE, make_test_settings


ROOT = Path(__file__).resolve().parents[2]
PHASE7_TEMPLATE = ROOT / "infra" / "phase7-ecs-api.yaml"
PHASE13_TEMPLATE = ROOT / "infra" / "phase13-github-oidc.yaml"
AGENTCORE_EXAMPLE = ROOT / "agent-runtime" / "env.agentcore.example.json"
AGENTCORE_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-agentcore.yml"
AGENTCORE_BUILDER = (
    ROOT / ".github" / "scripts" / "build-agentcore-update-input.py"
)
EXAMPLE_PARAMETERS = ROOT / "infra" / "parameters" / "dev.example.json"

TICKET_ACTIONS = {
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:Query",
    "dynamodb:ConditionCheckItem",
    "dynamodb:DescribeTable",
}
TICKET_TABLE_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:dynamodb:${AWS::Region}:"
        "${AWS::AccountId}:table/${TicketsTableName}"
    )
}
TICKET_INDEX_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:dynamodb:${AWS::Region}:"
        "${AWS::AccountId}:table/${TicketsTableName}/index/*"
    )
}
CONVERSATION_TABLE_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:dynamodb:${AWS::Region}:"
        "${AWS::AccountId}:table/${ConversationMessagesTableName}"
    )
}
CONVERSATION_INDEX_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:dynamodb:${AWS::Region}:"
        "${AWS::AccountId}:table/${ConversationMessagesTableName}/index/*"
    )
}
VOICE_BUCKET_ARN = {"Fn::GetAtt": "VoiceMediaBucket.Arn"}
VOICE_INPUT_ARN = {"Fn::Sub": "${VoiceMediaBucket.Arn}/voice-input/*"}
VOICE_QUEUE_ARN = {"Fn::GetAtt": "VoiceProcessingQueue.Arn"}
VOICE_TRANSCRIPTION_JOB_ARN = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:transcribe:${AWS::Region}:"
        "${AWS::AccountId}:transcription-job/${ProjectName}-whatsapp-voice-*"
    )
}
VOICE_TRANSCRIBE_ROLE_ARN_PATTERN = (
    "^$|^arn:(aws|aws-us-gov|aws-cn):iam::[0-9]{12}:"
    "role/[A-Za-z0-9+=,.@*-]+(?:/[A-Za-z0-9+=,.@*-]+)*$"
)


class CloudFormationLoader(yaml.SafeLoader):
    pass


class WorkflowLoader(yaml.SafeLoader):
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
WorkflowLoader.yaml_implicit_resolvers = {
    key: [
        resolver
        for resolver in resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_template(path=PHASE7_TEMPLATE):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=CloudFormationLoader)


def load_workflow():
    return yaml.load(
        AGENTCORE_WORKFLOW.read_text(encoding="utf-8"),
        Loader=WorkflowLoader,
    )


def parameter_map(path):
    return {
        entry["ParameterKey"]: entry["ParameterValue"]
        for entry in json.loads(path.read_text(encoding="utf-8"))
    }


def environment_map(template):
    definitions = template["Resources"]["BackendTaskDefinition"]["Properties"][
        "ContainerDefinitions"
    ]
    return {
        entry["Name"]: entry["Value"]
        for entry in definitions[0]["Environment"]
    }


def voice_worker_environment_map(template):
    definitions = template["Resources"]["WhatsAppVoiceWorkerTaskDefinition"][
        "Properties"
    ]["ContainerDefinitions"]
    return {
        entry["Name"]: entry["Value"]
        for entry in definitions[0]["Environment"]
    }


def container_secrets(template):
    definitions = template["Resources"]["BackendTaskDefinition"]["Properties"][
        "ContainerDefinitions"
    ]
    return definitions[0]["Secrets"]


def ticket_statement(template, policy_name):
    statements = template["Resources"][policy_name]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    return next(statement for statement in statements if statement.get("Sid") == "UseTicketsTable")


def conversation_statement(template):
    statements = template["Resources"]["EcsTaskDynamoDbPolicy"]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    return next(
        statement
        for statement in statements
        if statement.get("Sid") == "UseConversationMessagesTable"
    )


def policy_statements(template, policy_name):
    return template["Resources"][policy_name]["Properties"][
        "PolicyDocument"
    ]["Statement"]


def statement_by_sid(template, policy_name, sid):
    return next(
        statement
        for statement in policy_statements(template, policy_name)
        if statement.get("Sid") == sid
    )


def workflow_step(workflow, name):
    steps = workflow["jobs"]["deploy"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def test_phase7_ticket_parameters_condition_and_outputs():
    template = load_template()
    parameters = template["Parameters"]

    assert parameters["TicketsTableName"]["Default"] == "fyp-dev-Tickets"
    assert parameters["CreateTicketsTable"]["Default"] == "false"
    assert parameters["CreateTicketsTable"]["AllowedValues"] == ["true", "false"]
    assert parameters["SupportPhoneNumber"]["Default"] == ""
    assert template["Conditions"]["ShouldCreateTicketsTable"] == {
        "Fn::Equals": [{"Ref": "CreateTicketsTable"}, "true"]
    }
    assert template["Outputs"]["TicketsTableName"]["Value"] == {
        "Ref": "TicketsTableName"
    }
    assert template["Outputs"]["TicketsTableArn"]["Value"] == TICKET_TABLE_ARN


def test_phase7_ticket_table_matches_repository_schema_and_safety():
    template = load_template()
    resource = template["Resources"]["TicketsTable"]
    properties = resource["Properties"]

    assert resource["Type"] == "AWS::DynamoDB::Table"
    assert resource["Condition"] == "ShouldCreateTicketsTable"
    assert resource["DeletionPolicy"] == "Retain"
    assert resource["UpdateReplacePolicy"] == "Retain"
    assert properties["TableName"] == {"Ref": "TicketsTableName"}
    assert properties["BillingMode"] == "PAY_PER_REQUEST"
    assert properties["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert properties["AttributeDefinitions"] == [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "S"},
        {"AttributeName": "GSI2PK", "AttributeType": "S"},
        {"AttributeName": "GSI2SK", "AttributeType": "S"},
    ]
    assert properties["GlobalSecondaryIndexes"] == [
        {
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        },
        {
            "IndexName": "GSI2",
            "KeySchema": [
                {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        },
    ]
    assert properties["TimeToLiveSpecification"] == {
        "AttributeName": "expires_at",
        "Enabled": True,
    }
    assert properties["SSESpecification"] == {"SSEEnabled": True}
    assert properties["PointInTimeRecoverySpecification"] == {
        "PointInTimeRecoveryEnabled": True
    }
    assert {"Key": "Project", "Value": {"Ref": "ProjectName"}} in properties[
        "Tags"
    ]
    assert "ProvisionedThroughput" not in properties
    assert "StreamSpecification" not in properties


def test_phase7_conversation_messages_table_matches_history_schema_and_safety():
    template = load_template()
    parameters = template["Parameters"]
    resource = template["Resources"]["ConversationMessagesTable"]
    properties = resource["Properties"]

    assert parameters["ConversationMessagesTableName"]["Default"] == (
        "fyp-dev-ConversationMessages"
    )
    assert parameters["CreateConversationMessagesTable"]["Default"] == "false"
    assert parameters["CreateConversationMessagesTable"]["AllowedValues"] == [
        "true",
        "false",
    ]
    assert template["Conditions"]["ShouldCreateConversationMessagesTable"] == {
        "Fn::Equals": [{"Ref": "CreateConversationMessagesTable"}, "true"]
    }
    assert resource["Type"] == "AWS::DynamoDB::Table"
    assert resource["Condition"] == "ShouldCreateConversationMessagesTable"
    assert resource["DeletionPolicy"] == "Retain"
    assert resource["UpdateReplacePolicy"] == "Retain"
    assert properties["TableName"] == {"Ref": "ConversationMessagesTableName"}
    assert properties["BillingMode"] == "PAY_PER_REQUEST"
    assert properties["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert properties["AttributeDefinitions"] == [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "S"},
    ]
    assert properties["GlobalSecondaryIndexes"] == [
        {
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ]
    assert properties["TimeToLiveSpecification"] == {
        "AttributeName": "expires_at",
        "Enabled": True,
    }
    assert properties["SSESpecification"] == {"SSEEnabled": True}
    assert properties["PointInTimeRecoverySpecification"] == {
        "PointInTimeRecoveryEnabled": True
    }
    assert template["Outputs"]["ConversationMessagesTableName"]["Value"] == {
        "Ref": "ConversationMessagesTableName"
    }
    assert template["Outputs"]["ConversationMessagesTableArn"]["Value"] == (
        CONVERSATION_TABLE_ARN
    )


def test_conversation_messages_iam_is_least_privilege_for_ecs():
    statement = conversation_statement(load_template())

    assert statement["Effect"] == "Allow"
    assert set(statement["Action"]) == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:DescribeTable",
    }
    assert statement["Resource"] == [
        CONVERSATION_TABLE_ARN,
        CONVERSATION_INDEX_ARN,
    ]
    serialized = json.dumps(statement)
    assert '"Resource": "*"' not in serialized
    assert "dynamodb:*" not in serialized
    assert "dynamodb:Scan" not in serialized
    assert "dynamodb:DeleteItem" not in serialized
    assert "dynamodb:BatchWriteItem" not in serialized


@pytest.mark.parametrize(
    "policy_name",
    ["EcsTaskDynamoDbPolicy", "AgentCoreExecutionDynamoDbPolicy"],
)
def test_ticket_iam_is_least_privilege_for_ecs_and_agentcore(policy_name):
    statement = ticket_statement(load_template(), policy_name)

    assert statement["Effect"] == "Allow"
    assert set(statement["Action"]) == TICKET_ACTIONS
    assert "dynamodb:ConditionCheckItem" in statement["Action"]
    assert "dynamodb:PutItem" in statement["Action"]
    assert "dynamodb:TransactWriteItems" not in statement["Action"]
    assert statement["Resource"] == [TICKET_TABLE_ARN, TICKET_INDEX_ARN]
    serialized = json.dumps(statement)
    assert '"Resource": "*"' not in serialized
    assert "dynamodb:*" not in serialized
    assert "dynamodb:Scan" not in serialized
    assert not {
        "dynamodb:CreateTable",
        "dynamodb:DeleteTable",
        "dynamodb:UpdateTable",
        "dynamodb:BatchWriteItem",
    } & set(statement["Action"])


def test_ecs_environment_uses_effective_ticket_parameters():
    environment = environment_map(load_template())

    assert environment["TICKETS_TABLE_NAME"] == {"Ref": "TicketsTableName"}
    assert environment["CONVERSATION_MESSAGES_TABLE_NAME"] == {
        "Ref": "ConversationMessagesTableName"
    }
    assert environment["CONVERSATION_MESSAGE_TTL_DAYS"] == "90"
    assert environment["SUPPORT_PHONE_NUMBER"] == {
        "Ref": "SupportPhoneNumber"
    }
    for existing in (
        "ENVIRONMENT",
        "AWS_REGION",
        "AGENT_REQUESTS_TABLE_NAME",
        "CONVERSATION_MESSAGES_TABLE_NAME",
        "MENU_TABLE_NAME",
        "CARTS_TABLE_NAME",
        "ORDERS_TABLE_NAME",
        "CUSTOMERS_TABLE_NAME",
        "AGENT_SESSIONS_TABLE_NAME",
        "MENU_SESSIONS_TABLE_NAME",
        "AUDIT_TABLE_NAME",
    ):
        assert existing in environment


def test_voice_media_bucket_is_temporary_private_and_encrypted():
    template = load_template()
    resource = template["Resources"]["VoiceMediaBucket"]
    properties = resource["Properties"]

    assert resource["Type"] == "AWS::S3::Bucket"
    assert resource["DeletionPolicy"] == "Delete"
    assert resource["UpdateReplacePolicy"] == "Delete"
    assert properties["BucketName"] == {
        "Fn::Sub": "${ProjectName}-voice-${AWS::AccountId}-${AWS::Region}"
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
    assert properties["LifecycleConfiguration"] == {
        "Rules": [{
            "Id": "DeleteTemporaryVoiceInput",
            "Status": "Enabled",
            "Prefix": "voice-input/",
            "ExpirationInDays": 1,
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
        }]
    }
    assert "WebsiteConfiguration" not in properties
    assert "VersioningConfiguration" not in properties

    bucket_policy = template["Resources"]["VoiceMediaBucketPolicy"]
    assert bucket_policy["Type"] == "AWS::S3::BucketPolicy"
    assert bucket_policy["Properties"]["Bucket"] == {"Ref": "VoiceMediaBucket"}
    assert bucket_policy["Properties"]["PolicyDocument"]["Statement"] == [
        {
            "Sid": "DenyInsecureTransport",
            "Effect": "Deny",
            "Principal": "*",
            "Action": "s3:*",
            "Resource": [
                VOICE_BUCKET_ARN,
                {"Fn::Sub": "${VoiceMediaBucket.Arn}/*"},
            ],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        },
        {
            "Fn::If": [
                "HasVoiceTranscribeRoleArn",
                {
                    "Sid": "AllowConfiguredTranscribeRoleToListVoiceInput",
                    "Effect": "Allow",
                    "Principal": {"AWS": {"Ref": "VoiceTranscribeRoleArn"}},
                    "Action": ["s3:ListBucket"],
                    "Resource": VOICE_BUCKET_ARN,
                    "Condition": {
                        "StringLike": {"s3:prefix": "voice-input/*"}
                    },
                },
                {"Ref": "AWS::NoValue"},
            ]
        },
        {
            "Fn::If": [
                "HasVoiceTranscribeRoleArn",
                {
                    "Sid": "AllowConfiguredTranscribeRoleToReadVoiceInput",
                    "Effect": "Allow",
                    "Principal": {"AWS": {"Ref": "VoiceTranscribeRoleArn"}},
                    "Action": ["s3:GetObject"],
                    "Resource": VOICE_INPUT_ARN,
                },
                {"Ref": "AWS::NoValue"},
            ]
        },
    ]


def test_voice_queues_are_encrypted_bounded_and_have_exact_redrive_target():
    template = load_template()
    resources = template["Resources"]
    queue = resources["VoiceProcessingQueue"]
    dead_letter_queue = resources["VoiceProcessingDeadLetterQueue"]

    assert queue["Type"] == "AWS::SQS::Queue"
    assert dead_letter_queue["Type"] == "AWS::SQS::Queue"
    assert queue["Properties"]["SqsManagedSseEnabled"] is True
    assert dead_letter_queue["Properties"]["SqsManagedSseEnabled"] is True
    assert queue["Properties"]["MessageRetentionPeriod"] == 86400
    assert dead_letter_queue["Properties"]["MessageRetentionPeriod"] == 1209600
    assert queue["Properties"]["VisibilityTimeout"] >= 180
    assert queue["Properties"]["ReceiveMessageWaitTimeSeconds"] == 20
    assert queue["Properties"]["RedrivePolicy"] == {
        "deadLetterTargetArn": {
            "Fn::GetAtt": "VoiceProcessingDeadLetterQueue.Arn"
        },
        "maxReceiveCount": 3,
    }
    assert not any(
        resource.get("Type") == "AWS::SQS::QueuePolicy"
        for resource in resources.values()
    )


def test_voice_iam_is_split_between_web_and_worker_roles():
    template = load_template()
    web_policy = template["Resources"]["EcsTaskVoicePolicy"]
    worker_policy = template["Resources"]["WhatsAppVoiceWorkerVoicePolicy"]

    assert web_policy["Properties"]["Roles"] == [{"Ref": "EcsTaskRole"}]
    assert worker_policy["Properties"]["Roles"] == [
        {"Ref": "WhatsAppVoiceWorkerTaskRole"}
    ]

    assert statement_by_sid(
        template, "EcsTaskVoicePolicy", "PersistVoiceJobState"
    ) == {
        "Sid": "PersistVoiceJobState",
        "Effect": "Allow",
        "Action": [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
        ],
        "Resource": [
            {"Fn::GetAtt": "WhatsAppVoiceJobsTable.Arn"},
            {"Fn::Sub": "${WhatsAppVoiceJobsTable.Arn}/index/*"},
        ],
    }
    assert statement_by_sid(
        template, "EcsTaskVoicePolicy", "SubmitVoiceProcessingJob"
    ) == {
        "Sid": "SubmitVoiceProcessingJob",
        "Effect": "Allow",
        "Action": ["sqs:SendMessage"],
        "Resource": VOICE_QUEUE_ARN,
    }

    serialized_web_policy = json.dumps(web_policy)
    for forbidden_action in (
        "s3:GetBucketLocation",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes",
        "transcribe:StartTranscriptionJob",
        "transcribe:GetTranscriptionJob",
        "transcribe:DeleteTranscriptionJob",
    ):
        assert forbidden_action not in serialized_web_policy

    assert statement_by_sid(
        template,
        "WhatsAppVoiceWorkerVoicePolicy",
        "GetVoiceMediaBucketLocation",
    ) == {
        "Sid": "GetVoiceMediaBucketLocation",
        "Effect": "Allow",
        "Action": ["s3:GetBucketLocation"],
        "Resource": VOICE_BUCKET_ARN,
    }
    assert statement_by_sid(
        template,
        "WhatsAppVoiceWorkerVoicePolicy",
        "ManageTemporaryVoiceInput",
    ) == {
        "Sid": "ManageTemporaryVoiceInput",
        "Effect": "Allow",
        "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
        "Resource": VOICE_INPUT_ARN,
    }
    assert statement_by_sid(
        template,
        "WhatsAppVoiceWorkerVoicePolicy",
        "ConsumeAndRecoverVoiceQueue",
    ) == {
        "Sid": "ConsumeAndRecoverVoiceQueue",
        "Effect": "Allow",
        "Action": [
            "sqs:SendMessage",
            "sqs:ReceiveMessage",
            "sqs:DeleteMessage",
            "sqs:ChangeMessageVisibility",
            "sqs:GetQueueAttributes",
        ],
        "Resource": VOICE_QUEUE_ARN,
    }

    serialized_worker_policy = json.dumps(worker_policy)
    assert "s3:*" not in serialized_worker_policy
    assert "sqs:*" not in serialized_worker_policy
    assert "transcribe:*" not in serialized_worker_policy
    assert "iam:PassRole" not in serialized_worker_policy
    assert '${VoiceMediaBucket.Arn}/*' not in serialized_worker_policy
    assert '${VoiceMediaBucket.Arn}/voice-input/*' in serialized_worker_policy

    execution_role_refs = (
        {"Ref": "EcsTaskExecutionRole"},
        {"Ref": "WhatsAppVoiceWorkerExecutionRole"},
    )
    execution_policies = [
        resource
        for resource in template["Resources"].values()
        if resource.get("Type") == "AWS::IAM::Policy"
        and any(
            role_ref in resource.get("Properties", {}).get("Roles", [])
            for role_ref in execution_role_refs
        )
    ]
    assert execution_policies

    serialized_execution_policies = json.dumps(execution_policies)
    for service_prefix in ("s3:", "sqs:", "transcribe:"):
        assert service_prefix not in serialized_execution_policies


def test_voice_transcribe_wildcard_is_isolated_and_job_access_is_scoped():
    template = load_template()
    policy_name = "WhatsAppVoiceWorkerVoicePolicy"

    start = statement_by_sid(
        template,
        policy_name,
        "StartVoiceTranscriptionJob",
    )
    manage = statement_by_sid(
        template,
        policy_name,
        "ManageScopedVoiceTranscriptionJobs",
    )

    assert start == {
        "Sid": "StartVoiceTranscriptionJob",
        "Effect": "Allow",
        "Action": ["transcribe:StartTranscriptionJob"],
        "Resource": "*",
    }
    assert manage == {
        "Sid": "ManageScopedVoiceTranscriptionJobs",
        "Effect": "Allow",
        "Action": [
            "transcribe:GetTranscriptionJob",
            "transcribe:DeleteTranscriptionJob",
        ],
        "Resource": VOICE_TRANSCRIPTION_JOB_ARN,
    }
    assert all(
        statement["Sid"] == "StartVoiceTranscriptionJob"
        for statement in policy_statements(template, policy_name)
        if statement.get("Resource") == "*"
    )

    serialized_web_policy = json.dumps(
        template["Resources"]["EcsTaskVoicePolicy"]
    )
    assert "transcribe:" not in serialized_web_policy

    serialized_template = json.dumps(template)
    assert "iam:PassRole" not in serialized_template
    assert "transcribe.amazonaws.com" not in serialized_template


def test_optional_cross_account_transcribe_role_is_exact_and_worker_only():
    template = load_template()
    parameter = template["Parameters"]["VoiceTranscribeRoleArn"]

    assert parameter == {
        "Type": "String",
        "Default": "",
        "AllowedPattern": VOICE_TRANSCRIBE_ROLE_ARN_PATTERN,
        "Description": (
            "Optional cross-account IAM role assumed only by the WhatsApp "
            "voice worker for Amazon Transcribe."
        ),
    }
    assert template["Conditions"]["HasVoiceTranscribeRoleArn"] == {
        "Fn::Not": [{"Fn::Equals": [{"Ref": "VoiceTranscribeRoleArn"}, ""]}]
    }

    conditional_assume = next(
        statement["Fn::If"]
        for statement in policy_statements(
            template,
            "WhatsAppVoiceWorkerVoicePolicy",
        )
        if "Fn::If" in statement
        and statement["Fn::If"][1].get("Sid")
        == "AssumeConfiguredVoiceTranscribeRole"
    )
    assert conditional_assume == [
        "HasVoiceTranscribeRoleArn",
        {
            "Sid": "AssumeConfiguredVoiceTranscribeRole",
            "Effect": "Allow",
            "Action": ["sts:AssumeRole"],
            "Resource": {"Ref": "VoiceTranscribeRoleArn"},
        },
        {"Ref": "AWS::NoValue"},
    ]

    for role_name in (
        "EcsTaskRole",
        "EcsTaskExecutionRole",
        "WhatsAppVoiceWorkerExecutionRole",
    ):
        attached = [
            resource
            for resource in template["Resources"].values()
            if resource.get("Type") == "AWS::IAM::Policy"
            and {"Ref": role_name}
            in resource.get("Properties", {}).get("Roles", [])
        ]
        assert "sts:AssumeRole" not in json.dumps(attached)

    worker_environment = voice_worker_environment_map(template)
    backend_environment = environment_map(template)
    assert worker_environment["VOICE_TRANSCRIBE_ROLE_ARN"] == {
        "Ref": "VoiceTranscribeRoleArn"
    }
    assert "VOICE_TRANSCRIBE_ROLE_ARN" not in backend_environment
    assert template["Parameters"]["WhatsAppVoiceEnabled"]["Default"] == "false"
    assert template["Parameters"]["WorkerDesiredCount"]["Default"] == 0


def test_cross_account_option_preserves_persistent_resource_identities():
    resources = load_template()["Resources"]
    expected_types = {
        "BackendRepository": "AWS::ECR::Repository",
        "AgentRuntimeRepository": "AWS::ECR::Repository",
        "VoiceMediaBucket": "AWS::S3::Bucket",
        "VoiceProcessingQueue": "AWS::SQS::Queue",
        "VoiceProcessingDeadLetterQueue": "AWS::SQS::Queue",
        "WhatsAppVoiceJobsTable": "AWS::DynamoDB::Table",
        "EcsCluster": "AWS::ECS::Cluster",
        "BackendService": "AWS::ECS::Service",
        "WhatsAppVoiceWorkerService": "AWS::ECS::Service",
        "BackendHttpApi": "AWS::ApiGatewayV2::Api",
        "BackendDiscoveryService": "AWS::ServiceDiscovery::Service",
    }

    for logical_id, resource_type in expected_types.items():
        assert resources[logical_id]["Type"] == resource_type

    assert resources["VoiceMediaBucket"]["Properties"]["BucketName"] == {
        "Fn::Sub": "${ProjectName}-voice-${AWS::AccountId}-${AWS::Region}"
    }
    assert resources["VoiceProcessingQueue"]["Properties"]["QueueName"] == {
        "Fn::Sub": "${ProjectName}-whatsapp-voice-processing"
    }
    assert resources["VoiceProcessingDeadLetterQueue"]["Properties"][
        "QueueName"
    ] == {"Fn::Sub": "${ProjectName}-whatsapp-voice-processing-dlq"}
    assert resources["WhatsAppVoiceJobsTable"]["Properties"]["TableName"] == {
        "Ref": "WhatsAppVoiceJobsTableName"
    }


def test_voice_environment_defaults_disabled_and_outputs_are_wired():
    template = load_template()
    parameter = template["Parameters"]["WhatsAppVoiceEnabled"]
    environment = environment_map(template)

    assert parameter == {
        "Type": "String",
        "Default": "false",
        "AllowedValues": ["true", "false"],
        "Description": (
            "Keep disabled until the inbound voice backend and durable worker "
            "are deployed."
        ),
    }
    assert environment["WHATSAPP_VOICE_ENABLED"] == {
        "Ref": "WhatsAppVoiceEnabled"
    }
    assert environment["VOICE_MEDIA_BUCKET_NAME"] == {"Ref": "VoiceMediaBucket"}
    assert environment["VOICE_MEDIA_INPUT_PREFIX"] == "voice-input/"
    assert environment["VOICE_JOB_QUEUE_URL"] == {"Ref": "VoiceProcessingQueue"}
    assert environment["VOICE_MAX_MEDIA_BYTES"] == "10485760"
    assert environment["VOICE_DOWNLOAD_TIMEOUT_SECONDS"] == "10"
    assert environment["VOICE_TRANSCRIPTION_TIMEOUT_SECONDS"] == "180"
    assert "VOICE_MEDIA_OUTPUT_PREFIX" not in environment
    assert environment["AGENTCORE_RUNTIME_ARN"] == {"Ref": "AgentCoreRuntimeArn"}
    assert environment["AGENTFLO_GATEWAY_BASE_URL"] == {
        "Ref": "AgentfloGatewayBaseUrl"
    }
    assert environment["AGENTFLO_GATEWAY_TENANT_ID"] == {
        "Ref": "AgentfloGatewayTenantId"
    }
    assert environment["MENU_TABLE_NAME"] == {"Ref": "MenuTableName"}
    assert environment["AGENT_REQUESTS_TABLE_NAME"] == {
        "Ref": "AgentRequestsTableName"
    }
    assert environment["ADMIN_USERNAME"] == {"Ref": "AdminUsername"}

    outputs = template["Outputs"]
    assert outputs["VoiceMediaBucketName"]["Value"] == {"Ref": "VoiceMediaBucket"}
    assert outputs["VoiceMediaBucketArn"]["Value"] == VOICE_BUCKET_ARN
    assert outputs["VoiceProcessingQueueUrl"]["Value"] == {
        "Ref": "VoiceProcessingQueue"
    }
    assert outputs["VoiceProcessingQueueArn"]["Value"] == VOICE_QUEUE_ARN
    assert outputs["VoiceProcessingDeadLetterQueueArn"]["Value"] == {
        "Fn::GetAtt": "VoiceProcessingDeadLetterQueue.Arn"
    }


def test_agentflo_whatsapp_webhook_secret_is_conditionally_injected():
    template = load_template()
    parameter = template["Parameters"]["AgentfloWhatsAppWebhookSecretArn"]

    assert parameter["Type"] == "String"
    assert parameter["Default"] == ""
    assert template["Conditions"]["HasAgentfloWhatsAppWebhookSecret"] == {
        "Fn::Not": [
            {
                "Fn::Equals": [
                    {"Ref": "AgentfloWhatsAppWebhookSecretArn"},
                    "",
                ]
            }
        ]
    }

    assert {
        "Fn::If": [
            "HasAgentfloWhatsAppWebhookSecret",
            {
                "Name": "AGENTFLO_WHATSAPP_WEBHOOK_SECRET",
                "ValueFrom": {"Ref": "AgentfloWhatsAppWebhookSecretArn"},
            },
            {"Ref": "AWS::NoValue"},
        ]
    } in container_secrets(template)
    assert "AGENTFLO_WHATSAPP_WEBHOOK_SECRET" not in environment_map(template)

    policy = template["Resources"][
        "EcsTaskExecutionAgentfloWhatsAppWebhookSecretPolicy"
    ]
    assert policy["Condition"] == "HasAgentfloWhatsAppWebhookSecret"
    statement = policy["Properties"]["PolicyDocument"]["Statement"]
    assert statement == [
        {
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": {"Ref": "AgentfloWhatsAppWebhookSecretArn"},
        }
    ]
    serialized = json.dumps(policy)
    assert '"Resource": "*"' not in serialized
    assert "secretsmanager:*" not in serialized


def test_agentflo_gateway_configuration_and_secret_are_wired_to_ecs():
    template = load_template()
    parameters = template["Parameters"]
    environment = environment_map(template)

    assert parameters["AgentfloGatewayBaseUrl"]["Default"] == (
        "https://communicationgateway.agentflo.com"
    )
    assert parameters["AgentfloGatewayTenantId"]["Default"] == "fyp-dev"
    assert parameters["AgentfloGatewayAgentId"]["Default"] == (
        "restaurant-agent"
    )
    assert parameters["AgentfloGatewayActorId"]["Default"] == (
        "restaurant-agent"
    )
    assert parameters["AgentfloGatewayApiKeySecretArn"]["Default"] == ""
    assert template["Conditions"]["HasAgentfloGatewayApiKeySecret"] == {
        "Fn::Not": [
            {
                "Fn::Equals": [
                    {"Ref": "AgentfloGatewayApiKeySecretArn"},
                    "",
                ]
            }
        ]
    }

    assert environment["AGENTFLO_GATEWAY_BASE_URL"] == {
        "Ref": "AgentfloGatewayBaseUrl"
    }
    assert environment["AGENTFLO_GATEWAY_TENANT_ID"] == {
        "Ref": "AgentfloGatewayTenantId"
    }
    assert environment["AGENTFLO_GATEWAY_AGENT_ID"] == {
        "Ref": "AgentfloGatewayAgentId"
    }
    assert environment["AGENTFLO_GATEWAY_ACTOR_ID"] == {
        "Ref": "AgentfloGatewayActorId"
    }
    assert "AGENTFLO_GATEWAY_API_KEY" not in environment
    assert {
        "Fn::If": [
            "HasAgentfloGatewayApiKeySecret",
            {
                "Name": "AGENTFLO_GATEWAY_API_KEY",
                "ValueFrom": {"Ref": "AgentfloGatewayApiKeySecretArn"},
            },
            {"Ref": "AWS::NoValue"},
        ]
    } in container_secrets(template)

    policy = template["Resources"]["EcsTaskExecutionAgentfloGatewayApiKeyPolicy"]
    assert policy["Condition"] == "HasAgentfloGatewayApiKeySecret"
    assert policy["Properties"]["PolicyDocument"]["Statement"] == [
        {
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": {"Ref": "AgentfloGatewayApiKeySecretArn"},
        }
    ]
    serialized = json.dumps(policy)
    assert '"Resource": "*"' not in serialized
    assert "secretsmanager:*" not in serialized


def test_existing_table_mode_has_no_conditional_resource_reference():
    template = load_template()

    for policy_name in (
        "EcsTaskDynamoDbPolicy",
        "AgentCoreExecutionDynamoDbPolicy",
    ):
        resources = ticket_statement(template, policy_name)["Resource"]
        assert resources == [TICKET_TABLE_ARN, TICKET_INDEX_ARN]
        assert "GetAtt" not in json.dumps(resources)
    assert template["Outputs"]["TicketsTableArn"]["Value"] == TICKET_TABLE_ARN
    assert environment_map(template)["TICKETS_TABLE_NAME"] == {
        "Ref": "TicketsTableName"
    }


def test_tracked_parameter_example_includes_ticket_configuration_without_phone():
    example = parameter_map(EXAMPLE_PARAMETERS)

    assert example["WhatsAppVoiceEnabled"] == "false"
    assert example["ConversationMessagesTableName"] == (
        "fyp-dev-ConversationMessages"
    )
    assert example["CreateConversationMessagesTable"] == "false"
    assert example["TicketsTableName"] == "fyp-dev-Tickets"
    assert example["CreateTicketsTable"] == "false"
    assert example["SupportPhoneNumber"] == ""
    assert example["AgentfloWhatsAppWebhookSecretArn"] == ""
    assert example["AgentfloGatewayBaseUrl"] == (
        "https://communicationgateway.agentflo.com"
    )
    assert example["AgentfloGatewayTenantId"] == "fyp-dev"
    assert example["AgentfloGatewayAgentId"] == "restaurant-agent"
    assert example["AgentfloGatewayActorId"] == "restaurant-agent"
    assert example["AgentfloGatewayApiKeySecretArn"] == ""
    assert example["VoiceTranscribeRoleArn"] == ""


def test_agentcore_example_and_deployment_wire_required_runtime_environment():
    example = json.loads(AGENTCORE_EXAMPLE.read_text(encoding="utf-8"))
    workflow = load_workflow()
    builder = AGENTCORE_BUILDER.read_text(encoding="utf-8")

    assert example["TICKETS_TABLE_NAME"] == "fyp-dev-Tickets"
    assert example["SUPPORT_PHONE_NUMBER"] == ""
    assert example["CONVERSATION_MESSAGES_TABLE_NAME"] == (
        "fyp-dev-ConversationMessages"
    )
    assert example["CONVERSATION_MESSAGE_TTL_DAYS"] == "90"
    assert "AGENT_REQUESTS_TABLE_NAME" in example
    assert "AGENTCORE_MEMORY_ID" in example
    assert "on" in workflow

    deploy = workflow["jobs"]["deploy"]
    assert deploy["env"]["AWS_REGION"] == "${{ vars.AWS_REGION }}"
    assert deploy["env"]["BACKEND_STACK_NAME"] == (
        "${{ vars.BACKEND_STACK_NAME || 'fyp-dev-backend-api' }}"
    )

    credentials = workflow_step(workflow, "Configure AWS credentials")
    assert credentials["uses"] == "aws-actions/configure-aws-credentials@v6"
    assert credentials["with"]["role-to-assume"] == (
        "${{ vars.AGENTCORE_DEPLOY_ROLE_ARN }}"
    )
    assert credentials["with"]["aws-region"] == "${{ vars.AWS_REGION }}"

    resolution = workflow_step(
        workflow,
        "Resolve runtime environment from backend stack",
    )["run"]
    assert 'aws cloudformation describe-stacks \\' in resolution
    assert '--stack-name "$BACKEND_STACK_NAME"' in resolution
    assert 'outputs.get("TicketsTableName", "")' in resolution
    assert '"ConversationMessagesTableName"' in resolution
    assert (
        '"CONVERSATION_MESSAGES_TABLE_NAME": conversation_messages_table_name'
        in resolution
    )
    assert '"CONVERSATION_MESSAGE_TTL_DAYS": "90"' in resolution
    assert '"SupportPhoneNumber" not in parameters' in resolution
    assert '"SUPPORT_PHONE_NUMBER": parameters["SupportPhoneNumber"]' in resolution
    assert '"agentcore-runtime-environment.json"' in resolution

    builder_step = workflow_step(workflow, "Build AgentCore update input")["run"]
    assert "--environment-file agentcore-runtime-environment.json" in builder_step
    assert "--environment-file" in builder

    step_names = {step["name"] for step in deploy["steps"]}
    assert {
        "Build and push AgentCore runtime image",
        "Capture current AgentCore runtime",
        "Update AgentCore runtime",
        "Wait for AgentCore runtime READY",
        "Write AgentCore deployment summary",
    } <= step_names

    deployment_commands = "\n".join(
        step.get("run", "") for step in deploy["steps"]
    )
    assert "aws cloudformation deploy" not in deployment_commands
    assert "aws cloudformation create-stack" not in deployment_commands
    assert "aws cloudformation update-stack" not in deployment_commands
    for runtime_environment_step in (resolution, builder_step):
        assert "arn:aws:" not in runtime_environment_step
        assert re.search(r"\b\d{12}\b", runtime_environment_step) is None


def test_agentcore_deploy_role_can_read_only_the_backend_stack():
    template = load_template(PHASE13_TEMPLATE)
    parameters = template["Parameters"]
    statements = template["Resources"]["AgentCoreDeploymentRole"]["Properties"][
        "Policies"
    ][0]["PolicyDocument"]["Statement"]
    statement = next(
        item
        for item in statements
        if item.get("Sid") == "ReadBackendStackConfiguration"
    )

    assert parameters["BackendStackName"]["Default"] == "fyp-dev-backend-api"
    assert statement["Action"] == ["cloudformation:DescribeStacks"]
    assert statement["Resource"] == {
        "Fn::Sub": (
            "arn:${AWS::Partition}:cloudformation:${AWS::Region}:"
            "${AWS::AccountId}:stack/${BackendStackName}/*"
        )
    }


def test_backend_settings_and_local_ticket_schema_stay_consistent():
    missing = dict(BASE)
    missing.pop("tickets_table_name")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **missing)

    settings = make_test_settings(support_phone_number="")
    assert settings.support_phone_number == ""
    local = next(
        definition
        for definition in table_definitions(settings)
        if definition["TableName"] == settings.tickets_table_name
    )
    production = load_template()["Resources"]["TicketsTable"]["Properties"]
    for key in (
        "KeySchema",
        "AttributeDefinitions",
        "GlobalSecondaryIndexes",
        "BillingMode",
    ):
        assert local[key] == production[key]
    assert production["TimeToLiveSpecification"]["AttributeName"] == "expires_at"


def test_backend_settings_and_local_conversation_schema_stay_consistent():
    missing = dict(BASE)
    missing.pop("conversation_messages_table_name")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **missing)

    settings = make_test_settings()
    local = next(
        definition
        for definition in table_definitions(settings)
        if definition["TableName"] == settings.conversation_messages_table_name
    )
    production = load_template()["Resources"]["ConversationMessagesTable"][
        "Properties"
    ]
    for key in (
        "KeySchema",
        "AttributeDefinitions",
        "GlobalSecondaryIndexes",
        "BillingMode",
    ):
        assert local[key] == production[key]
    assert production["TimeToLiveSpecification"]["AttributeName"] == "expires_at"

def test_project_name_is_safe_for_explicit_s3_bucket_names():
    template = load_template()

    assert template["Parameters"]["ProjectName"]["AllowedPattern"] == (
        "^[a-z][a-z0-9-]{1,31}$"
    )
