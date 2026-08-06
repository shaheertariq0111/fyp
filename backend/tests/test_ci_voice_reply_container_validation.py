from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class WorkflowLoader(yaml.SafeLoader):
    pass


WorkflowLoader.yaml_implicit_resolvers = {
    key: [
        resolver
        for resolver in resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def test_voice_reply_container_validation_is_in_required_backend_gate():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    build = workflow.index("- name: Build backend Docker image")
    validate = workflow.index(
        "- name: Validate Polly voice-reply container runtime"
    )
    health = workflow.index("- name: Start backend container")

    assert build < validate < health
    assert "name: CI / Quality gate" in workflow
    assert "- backend" in workflow[workflow.index("quality-gate:"):]
    validation = workflow[validate:health]
    assert "docker run --rm -i" in validation
    assert "docker push" not in validation
    assert "amazon-ecr-login" not in validation
    assert 'shutil.which("ffmpeg")' in validation
    assert 'shutil.which("ffprobe")' in validation
    assert 'username != "app"' in validation
    assert "uid == 0" in validation
    assert "VoiceReplyAudioConverter" in validation
    assert '"codec_name": "opus"' in validation
    assert '"sample_rate": "16000"' in validation
    assert '"channels": 1' in validation
    assert '"ogg" not in format_names' in validation


def test_ci_workflow_keeps_exact_required_check_name():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("name: CI / Quality gate") == 1

    parsed = yaml.load(workflow, Loader=WorkflowLoader)
    assert parsed["name"] == "CI"
    assert parsed["jobs"]["quality-gate"]["name"] == "CI / Quality gate"

    validation = next(
        step
        for step in parsed["jobs"]["backend"]["steps"]
        if step.get("name") == "Validate Polly voice-reply container runtime"
    )["run"]
    marker = "<<'PY'\n"
    assert marker in validation
    python_source = validation.split(marker, 1)[1].rsplit("\nPY", 1)[0]
    compile(python_source, "ci_voice_reply_validation.py", "exec")
