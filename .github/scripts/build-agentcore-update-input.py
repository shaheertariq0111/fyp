#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any


OPTIONAL_MUTABLE_FIELDS = (
    "description",
    "authorizerConfiguration",
    "requestHeaderConfiguration",
    "protocolConfiguration",
    "lifecycleConfiguration",
    "metadataConfiguration",
    "environmentVariables",
    "filesystemConfigurations",
)

RESPONSE_ONLY_FIELDS = {
    "agentRuntimeArn",
    "agentRuntimeName",
    "agentRuntimeVersion",
    "status",
    "failureReason",
    "createdAt",
    "lastUpdatedAt",
    "workloadIdentityDetails",
}

FULL_GITHUB_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CLIENT_TOKEN_STRUCTURE_RE = re.compile(r"^[A-Za-z0-9](?:-*[A-Za-z0-9])*$")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def require_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"current runtime is missing object field {key!r}")
    return value


def require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"current runtime is missing string field {key!r}")
    return value


def validate_client_token(client_token: str) -> None:
    if not 33 <= len(client_token) <= 256:
        raise ValueError("client token must be 33-256 characters")
    if not CLIENT_TOKEN_STRUCTURE_RE.fullmatch(client_token):
        raise ValueError("client token must contain only letters, numbers, and hyphens with no leading or trailing hyphen")


def validate_image_uri(
    *,
    image_uri: str,
    expected_repository: str,
    mode: str,
    github_sha: str | None,
) -> None:
    if mode not in {"deployment", "rollback"}:
        raise ValueError("mode must be deployment or rollback")
    if "@sha256:" in image_uri:
        raise ValueError("image URI must use a tag, not a digest reference")
    if image_uri == expected_repository:
        raise ValueError("image URI must include a non-empty tag")
    expected_prefix = f"{expected_repository}:"
    if not image_uri.startswith(expected_prefix):
        raise ValueError("new image URI must belong to the expected ECR repository")
    tag = image_uri[len(expected_prefix) :]
    if not tag:
        raise ValueError("image URI must include a non-empty tag")
    if mode == "deployment":
        if github_sha is None:
            raise ValueError("github SHA is required in deployment mode")
        if not FULL_GITHUB_SHA_RE.fullmatch(github_sha):
            raise ValueError("github SHA must be the full 40-character lowercase hex commit SHA")
        if tag != github_sha:
            raise ValueError("new image tag must equal the full GitHub SHA in deployment mode")


def validate_runtime_status(status: str, mode: str) -> None:
    if mode == "deployment" and status != "READY":
        raise ValueError(f"deployment mode requires current runtime status READY; current status is {status!r}")
    if mode == "rollback" and status not in {"READY", "UPDATE_FAILED"}:
        raise ValueError(
            "rollback mode requires current runtime status READY or UPDATE_FAILED; "
            f"current status is {status!r}"
        )


def build_update_input(
    *,
    current: dict[str, Any],
    image_uri: str,
    agent_runtime_id: str,
    expected_runtime_arn: str,
    expected_role_arn: str,
    expected_repository: str,
    mode: str,
    github_sha: str | None,
    client_token: str,
) -> dict[str, Any]:
    validate_image_uri(
        image_uri=image_uri,
        expected_repository=expected_repository,
        mode=mode,
        github_sha=github_sha,
    )
    validate_client_token(client_token)

    current_id = require_string(current, "agentRuntimeId")
    if current_id != agent_runtime_id:
        raise ValueError("current runtime ID does not match the configured target")
    current_arn = require_string(current, "agentRuntimeArn")
    if current_arn != expected_runtime_arn:
        raise ValueError("current runtime ARN does not match the configured target")
    role_arn = require_string(current, "roleArn")
    if role_arn != expected_role_arn:
        raise ValueError("current execution role ARN does not match the configured target")
    validate_runtime_status(require_string(current, "status"), mode)

    artifact = require_mapping(current, "agentRuntimeArtifact")
    container_config = artifact.get("containerConfiguration")
    if not isinstance(container_config, dict):
        raise ValueError("current runtime artifact must be container-based")
    if not isinstance(container_config.get("containerUri"), str) or not container_config["containerUri"]:
        raise ValueError("current container artifact is missing containerUri")
    if "codeConfiguration" in artifact:
        raise ValueError("current runtime artifact must not be codeConfiguration-based")

    network_configuration = copy.deepcopy(require_mapping(current, "networkConfiguration"))
    if not isinstance(network_configuration.get("networkMode"), str):
        raise ValueError("networkConfiguration.networkMode is required")

    update_input: dict[str, Any] = {
        "agentRuntimeId": agent_runtime_id,
        "agentRuntimeArtifact": {
            "containerConfiguration": {
                "containerUri": image_uri,
            },
        },
        "roleArn": role_arn,
        "networkConfiguration": network_configuration,
        "clientToken": client_token,
    }

    for field in OPTIONAL_MUTABLE_FIELDS:
        if field in current and current[field] is not None:
            update_input[field] = copy.deepcopy(current[field])

    forbidden = RESPONSE_ONLY_FIELDS.intersection(update_input)
    if forbidden:
        raise ValueError(f"update input includes response-only fields: {sorted(forbidden)}")
    if update_input["roleArn"] != current["roleArn"]:
        raise ValueError("roleArn changed unexpectedly")
    if update_input["networkConfiguration"] != current["networkConfiguration"]:
        raise ValueError("networkConfiguration changed unexpectedly")
    return update_input


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a safe AgentCore UpdateAgentRuntime input JSON file.")
    parser.add_argument("--current-runtime", type=Path, required=True)
    parser.add_argument("--image-uri", required=True)
    parser.add_argument("--agent-runtime-id", required=True)
    parser.add_argument("--expected-runtime-arn", required=True)
    parser.add_argument("--expected-role-arn", required=True)
    parser.add_argument("--expected-ecr-repository", required=True)
    parser.add_argument("--mode", choices=("deployment", "rollback"), required=True)
    parser.add_argument("--github-sha")
    parser.add_argument("--client-token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current = load_json(args.current_runtime)
    update_input = build_update_input(
        current=current,
        image_uri=args.image_uri,
        agent_runtime_id=args.agent_runtime_id,
        expected_runtime_arn=args.expected_runtime_arn,
        expected_role_arn=args.expected_role_arn,
        expected_repository=args.expected_ecr_repository,
        mode=args.mode,
        github_sha=args.github_sha,
        client_token=args.client_token,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(update_input, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
