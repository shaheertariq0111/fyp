from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.receipt-lambda"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_receipt_lambda_image_uses_supported_runtime_and_processing_handler():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM public.ecr.aws/lambda/python:3.12" in dockerfile
    assert "COPY pyproject.toml ${LAMBDA_TASK_ROOT}/" in dockerfile
    assert "COPY src ${LAMBDA_TASK_ROOT}/src" in dockerfile
    assert "python -m pip install ." in dockerfile
    assert 'CMD ["src.lambda_handlers.receipt_processing.handler"]' in dockerfile


def test_receipt_lambda_image_excludes_ecs_only_runtime_dependencies():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8").lower()

    assert "uvicorn" not in dockerfile
    assert "ffmpeg" not in dockerfile
    assert "apt-get" not in dockerfile
    assert "node" not in dockerfile


def test_reportlab_is_installed_through_backend_package_dependency():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert "python -m pip install ." in dockerfile
    assert '"reportlab>=4.2,<5"' in pyproject
