from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_backend_image_installs_ffmpeg_before_non_root_runtime():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    install = dockerfile.index("apt-get install --no-install-recommends -y ffmpeg")
    user = dockerfile.index("USER app")
    assert install < user
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "USER root" not in dockerfile[user:]
    assert "HEALTHCHECK" in dockerfile
