from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "build-agentcore-update-input.py"
SPEC = importlib.util.spec_from_file_location("build_agentcore_update_input", SCRIPT_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)

SHA = "0123456789abcdef0123456789abcdef01234567"
REPOSITORY = "352306494518.dkr.ecr.us-east-1.amazonaws.com/fyp-dev-agent-runtime"
IMAGE_URI = f"{REPOSITORY}:{SHA}"
RUNTIME_ID = "fyp_dev_restaurant_agent-dwLwVnClBF"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:352306494518:runtime/fyp_dev_restaurant_agent-dwLwVnClBF"
ROLE_ARN = "arn:aws:iam::352306494518:role/fyp-dev-agentcore-execution"
TOKEN = f"agentcore-123456789-1-{SHA}"
LEGACY_IMAGE_URI = f"{REPOSITORY}:dev-phase12"


def current_runtime(**overrides):
    value = {
        "agentRuntimeArn": RUNTIME_ARN,
        "agentRuntimeName": "fyp_dev_restaurant_agent",
        "agentRuntimeId": RUNTIME_ID,
        "agentRuntimeVersion": "7",
        "createdAt": "2026-07-16T00:00:00Z",
        "lastUpdatedAt": "2026-07-16T00:00:00Z",
        "roleArn": ROLE_ARN,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "status": "READY",
        "agentRuntimeArtifact": {
            "containerConfiguration": {
                "containerUri": f"{REPOSITORY}:previous"
            }
        },
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 900},
        "environmentVariables": {
            "AGENTCORE_MEMORY_ID": "memory-id",
            "SESSION_TOKEN_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:352306494518:secret:example",
        },
        "workloadIdentityDetails": {"workloadIdentityArn": "arn:aws:iam::352306494518:role/workload"},
    }
    value.update(overrides)
    return value


def build(current=None, **overrides):
    params = {
        "current": current or current_runtime(),
        "image_uri": IMAGE_URI,
        "agent_runtime_id": RUNTIME_ID,
        "expected_runtime_arn": RUNTIME_ARN,
        "expected_role_arn": ROLE_ARN,
        "expected_repository": REPOSITORY,
        "mode": "deployment",
        "github_sha": SHA,
        "client_token": TOKEN,
    }
    params.update(overrides)
    return module.build_update_input(**params)


def test_builds_update_input_preserving_mutable_fields_without_response_fields():
    update_input = build()

    assert update_input["agentRuntimeId"] == RUNTIME_ID
    assert update_input["agentRuntimeArtifact"] == {
        "containerConfiguration": {"containerUri": IMAGE_URI}
    }
    assert update_input["roleArn"] == ROLE_ARN
    assert update_input["networkConfiguration"] == {"networkMode": "PUBLIC"}
    assert "requireServiceS3Endpoint" not in update_input["networkConfiguration"]
    assert update_input["protocolConfiguration"] == {"serverProtocol": "HTTP"}
    assert update_input["environmentVariables"]["AGENTCORE_MEMORY_ID"] == "memory-id"
    assert update_input["clientToken"] == TOKEN
    for field in module.RESPONSE_ONLY_FIELDS:
        assert field not in update_input


def test_valid_33_character_client_token_is_accepted():
    token = "a" * 33

    update_input = build(client_token=token)

    assert update_input["clientToken"] == token


@pytest.mark.parametrize(
    ("token", "message"),
    [
        ("a" * 32, "33-256"),
        ("a" * 257, "33-256"),
        ("valid-token-with-underscore_123456", "letters, numbers, and hyphens"),
    ],
)
def test_invalid_client_tokens_are_rejected(token, message):
    with pytest.raises(ValueError, match=message):
        build(client_token=token)


def test_workflow_generated_client_token_is_accepted():
    update_input = build(client_token=f"agentcore-987654321-2-{SHA}")

    assert update_input["clientToken"] == f"agentcore-987654321-2-{SHA}"


def test_deployment_mode_accepts_ready_status():
    update_input = build(mode="deployment", current=current_runtime(status="READY"))

    assert update_input["agentRuntimeId"] == RUNTIME_ID


def test_deployment_mode_rejects_update_failed_status():
    with pytest.raises(ValueError, match="deployment mode.*UPDATE_FAILED"):
        build(mode="deployment", current=current_runtime(status="UPDATE_FAILED"))


def test_rollback_mode_accepts_ready_status():
    update_input = build(mode="rollback", current=current_runtime(status="READY"), github_sha=None)

    assert update_input["agentRuntimeId"] == RUNTIME_ID


def test_rollback_mode_accepts_update_failed_status():
    update_input = build(mode="rollback", current=current_runtime(status="UPDATE_FAILED"), github_sha=None)

    assert update_input["agentRuntimeId"] == RUNTIME_ID


@pytest.mark.parametrize("status", ["UPDATING", "CREATING", "CREATE_FAILED", "DELETING"])
def test_rollback_mode_rejects_active_or_terminal_non_update_failed_statuses(status):
    with pytest.raises(ValueError, match=f"rollback mode.*{status}"):
        build(mode="rollback", current=current_runtime(status=status), github_sha=None)


def test_preserves_require_service_s3_endpoint_only_when_current_runtime_has_it():
    current = current_runtime(
        networkConfiguration={
            "networkMode": "VPC",
            "networkModeConfig": {
                "securityGroups": ["sg-0123456789abcdef0"],
                "subnets": ["subnet-0123456789abcdef0"],
                "requireServiceS3Endpoint": False,
            },
        }
    )

    update_input = build(current=current)

    assert update_input["networkConfiguration"] == current["networkConfiguration"]
    assert update_input["networkConfiguration"]["networkModeConfig"]["requireServiceS3Endpoint"] is False


def test_deployment_mode_accepts_exact_sha_tag():
    update_input = build(mode="deployment", image_uri=IMAGE_URI, github_sha=SHA)

    assert update_input["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"] == IMAGE_URI


def test_deployment_mode_rejects_legacy_tag():
    with pytest.raises(ValueError, match="full GitHub SHA"):
        build(mode="deployment", image_uri=LEGACY_IMAGE_URI, github_sha=SHA)


def test_deployment_mode_rejects_invalid_sha():
    with pytest.raises(ValueError, match="full 40-character"):
        build(mode="deployment", image_uri=IMAGE_URI, github_sha="abc123")


def test_rollback_mode_accepts_legacy_tag_in_expected_repository():
    update_input = build(mode="rollback", image_uri=LEGACY_IMAGE_URI, github_sha=None)

    assert update_input["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"] == LEGACY_IMAGE_URI


def test_rollback_mode_accepts_sha_tag():
    update_input = build(mode="rollback", image_uri=IMAGE_URI, github_sha=None)

    assert update_input["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"] == IMAGE_URI


def test_rollback_mode_rejects_different_repository():
    with pytest.raises(ValueError, match="expected ECR"):
        build(
            mode="rollback",
            image_uri="352306494518.dkr.ecr.us-east-1.amazonaws.com/other:dev-phase12",
            github_sha=None,
        )


@pytest.mark.parametrize(
    "image_uri",
    [
        f"{REPOSITORY}@sha256:{'a' * 64}",
        REPOSITORY,
        f"{REPOSITORY}:",
    ],
)
def test_rollback_mode_rejects_digest_only_and_untagged_references(image_uri):
    with pytest.raises(ValueError, match="tag|digest"):
        build(mode="rollback", image_uri=image_uri, github_sha=None)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"agentRuntimeId": "other-runtime-abcdefghij"}, "runtime ID"),
        ({"agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:352306494518:runtime/other"}, "runtime ARN"),
        ({"roleArn": "arn:aws:iam::352306494518:role/other"}, "execution role"),
        ({"status": "UPDATING"}, "deployment mode"),
        ({"agentRuntimeArtifact": {"codeConfiguration": {"runtime": "PYTHON_3_10"}}}, "container-based"),
    ],
)
def test_rejects_unexpected_current_runtime_shape(overrides, message):
    with pytest.raises(ValueError, match=message):
        build(current=current_runtime(**overrides))
