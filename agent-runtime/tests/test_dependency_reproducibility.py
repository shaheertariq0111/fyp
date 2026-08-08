from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS = ROOT / "agent-runtime" / "constraints.txt"
DOCKERFILE = ROOT / "agent-runtime" / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-agentcore.yml"
EXPECTED_VERSIONS = {
    "bedrock-agentcore": "1.19.0",
    "strands-agents": "1.50.2",
}


def _constraints() -> dict[str, str]:
    entries = {}
    for raw_line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        package, separator, version = line.partition("==")
        assert separator == "==", f"Constraint must be exact: {line}"
        entries[package] = version
    return entries


def _workflow_step(workflow: dict, name: str) -> dict:
    return next(
        step
        for step in workflow["jobs"]["validate"]["steps"]
        if step.get("name") == name
    )


def test_agentcore_constraints_pin_known_good_orchestration_versions():
    assert CONSTRAINTS.is_file()
    assert _constraints() == EXPECTED_VERSIONS


def test_agentcore_dockerfile_constrains_both_package_installations():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert (
        "COPY agent-runtime/constraints.txt ./agent-runtime/constraints.txt"
        in dockerfile
    )
    for package_path in ("./backend", "./agent-runtime"):
        assert (
            "pip install -c ./agent-runtime/constraints.txt " + package_path
            in dockerfile
        )


def test_agentcore_workflow_uses_and_verifies_constrained_versions():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    validate = workflow["jobs"]["validate"]
    setup_python = next(
        step
        for step in validate["steps"]
        if step.get("uses") == "actions/setup-python@v6"
    )
    cache_paths = setup_python["with"]["cache-dependency-path"].splitlines()
    assert "agent-runtime/constraints.txt" in cache_paths

    install = _workflow_step(
        workflow,
        "Install backend and AgentCore runtime dependencies",
    )["run"]
    for package_path in ('"./backend[dev]"', '"./agent-runtime[dev]"'):
        assert (
            "python -m pip install -c agent-runtime/constraints.txt -e "
            + package_path
            in install
        )

    host_verification = _workflow_step(
        workflow,
        "Verify AgentCore orchestration dependency versions",
    )["run"]
    image_verification = _workflow_step(
        workflow,
        "Verify AgentCore runtime image dependencies",
    )["run"]
    for verification in (host_verification, image_verification):
        assert "from importlib.metadata import version" in verification
        for package, required in EXPECTED_VERSIONS.items():
            assert f'"{package}": "{required}"' in verification
    assert "python -m pip check" in host_verification
    assert "--platform linux/arm64" in image_verification
    assert "--entrypoint python" in image_verification
