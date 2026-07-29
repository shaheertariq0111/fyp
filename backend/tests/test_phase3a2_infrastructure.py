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
    assert environment["SUPPORT_PHONE_NUMBER"] == {
        "Ref": "SupportPhoneNumber"
    }
    for existing in (
        "ENVIRONMENT",
        "AWS_REGION",
        "AGENT_REQUESTS_TABLE_NAME",
        "MENU_TABLE_NAME",
        "CARTS_TABLE_NAME",
        "ORDERS_TABLE_NAME",
        "CUSTOMERS_TABLE_NAME",
        "AGENT_SESSIONS_TABLE_NAME",
        "MENU_SESSIONS_TABLE_NAME",
        "AUDIT_TABLE_NAME",
    ):
        assert existing in environment


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


def test_agentcore_example_and_deployment_wire_ticket_environment():
    example = json.loads(AGENTCORE_EXAMPLE.read_text(encoding="utf-8"))
    workflow = load_workflow()
    builder = AGENTCORE_BUILDER.read_text(encoding="utf-8")

    assert example["TICKETS_TABLE_NAME"] == "fyp-dev-Tickets"
    assert example["SUPPORT_PHONE_NUMBER"] == ""
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
        "Resolve ticket environment from backend stack",
    )["run"]
    assert 'aws cloudformation describe-stacks \\' in resolution
    assert '--stack-name "$BACKEND_STACK_NAME"' in resolution
    assert 'outputs.get("TicketsTableName", "")' in resolution
    assert '"SupportPhoneNumber" not in parameters' in resolution
    assert '"SUPPORT_PHONE_NUMBER": parameters["SupportPhoneNumber"]' in resolution
    assert '"agentcore-ticket-environment.json"' in resolution

    builder_step = workflow_step(workflow, "Build AgentCore update input")["run"]
    assert "--environment-file agentcore-ticket-environment.json" in builder_step
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
    for ticket_step in (resolution, builder_step):
        assert "arn:aws:" not in ticket_step
        assert re.search(r"\b\d{12}\b", ticket_step) is None


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
