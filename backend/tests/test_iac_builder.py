"""services/iac_builder.py のユニットテスト。

正常系のZIP生成に加え、Step3で確認済みのYAMLインジェクション対策
（コロン・改行・二重引用符を含む値が混入しても構造が破壊されないこと）を
回帰テストとして固定化する。仕様書 Step6 要件2参照。
"""

import io
import zipfile

import pytest
from pydantic import ValidationError

from app.schemas import ClaudeJSONSubmission, ServiceDefinition
from app.services.iac_builder import build_iac_zip


def test_build_iac_zip_normal_case_contains_expected_files():
    submission = ClaudeJSONSubmission(
        project_name="sample-app",
        notes="正常系のサンプルです。",
        services=[
            ServiceDefinition(
                name="db", type="database", image_or_build="postgres:16-alpine", ports=["5432:5432"]
            ),
            ServiceDefinition(
                name="backend",
                type="backend",
                image_or_build="./backend",
                env={"DATABASE_URL": "postgresql://user:pass@db:5432/app"},
                ports=["8000:8000"],
            ),
        ],
    )

    zip_bytes = build_iac_zip(submission)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "docker-compose.yml" in names
        # image_or_build が "./backend" (ビルドコンテキスト) のサービスのみDockerfileが生成される。
        assert "backend/Dockerfile" in names
        # image_or_build が "postgres:16-alpine" (既存イメージ参照) のサービスは生成されない。
        assert "db/Dockerfile" not in names
        assert "NOTES.md" in names

        compose_content = zf.read("docker-compose.yml").decode("utf-8")
        assert "db:" in compose_content
        assert "backend:" in compose_content
        assert "postgres:16-alpine" in compose_content
        assert "5432:5432" in compose_content


def test_build_iac_zip_yaml_injection_is_contained():
    """YAML特殊文字（コロン・改行・二重引用符）を含む値を混入させても、
    生成されたdocker-compose.yml / Dockerfileの構造が破壊されないことを検証する
    （Step3で実施した検証の回帰テスト）。
    """
    submission = ClaudeJSONSubmission(
        project_name='evil"project\ninjected_key: injected_value',
        notes='notes with "quotes" and\nnewlines: yes',
        services=[
            ServiceDefinition(
                # nameはPydanticのfield_validatorで既に安全な文字種に限定されている。
                name="backend",
                type='backend"\ninjected_type_field: true',
                image_or_build='./backend"\nextra_top_level_key: injected',
                env={'FOO"\ninjected_env_key: true': 'bar"\nALSO_INJECTED: yes'},
                ports=['8000:8000"\n  - injected_port'],
            ),
        ],
    )

    zip_bytes = build_iac_zip(submission)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        compose_content = zf.read("docker-compose.yml").decode("utf-8")
        dockerfile_content = zf.read("backend/Dockerfile").decode("utf-8")

    # 注入を狙ったキーが、YAML上で独立したトップレベル/兄弟キーとして
    # 出現していない（=ダブルクォート文字列の中に閉じ込められている）ことを確認する。
    assert "\ninjected_key:" not in compose_content
    assert "\ninjected_type_field:" not in compose_content
    assert "\nextra_top_level_key:" not in compose_content
    assert "\ninjected_env_key:" not in compose_content
    assert "\nALSO_INJECTED:" not in compose_content
    assert "\n  - injected_port" not in compose_content

    # Dockerfile側: typeフィールド経由でコメント行を抜けて新しい命令が注入されていないか確認する。
    lines = dockerfile_content.splitlines()
    assert "injected_type_field" not in [ln.strip() for ln in lines[1:3]]


def test_service_name_validator_rejects_invalid_characters():
    """services[].name のバリデータが不正な値（空白・スラッシュ・コロン・改行等）を拒否する。"""
    for invalid_name in ["invalid name", "invalid/name", "invalid:name", "invalid\nname", "invalid!"]:
        with pytest.raises(ValidationError):
            ServiceDefinition(name=invalid_name, type="backend", image_or_build="./backend")


def test_service_name_validator_accepts_valid_characters():
    service = ServiceDefinition(name="my-service_01", type="backend", image_or_build="./backend")
    assert service.name == "my-service_01"
