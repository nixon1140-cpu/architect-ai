"""Claude Web由来の確定JSONから、Docker構成一式をメモリ上でZIP化する。

Jinja2テンプレート（app/templates/*.j2）に変数を流し込み、
io.BytesIO + zipfile.ZipFile によりディスクI/Oなしでメモリ上にZIPを構築する。
仕様書 9.2章 参照。

セキュリティ上の注意:
    schemas.ServiceDefinition.name は英数字・ハイフン・アンダースコアのみに制限
    済みだが、image_or_build・env・ports・notes 等の自由入力フィールドは
    コロンや改行などのYAML特殊文字を含み得る。これらをそのままテンプレートへ
    渡すとYAML構造を破壊する（YAMLインジェクション）ため、_yaml_quote() で
    フロースカラーとして安全にエスケープしてから渡す。
"""

import io
import zipfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas import ClaudeJSONSubmission, ServiceDefinition

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    # 出力はYAML/DockerfileでありHTMLではないため、autoescapeは無効化する。
    autoescape=select_autoescape(enabled_extensions=(), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _yaml_quote(value: str) -> str:
    """YAMLのダブルクォート付きフロースカラーとして安全な形にエスケープする。

    コロン・改行・ダブルクォート等のYAML特殊文字が生成物にそのまま混入して
    構造を破壊するのを防ぐ。
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{escaped}"'


def _sanitize_comment(value: str) -> str:
    """Dockerfileのコメント行に埋め込む値から改行を除去する。

    改行を許すと、コメント行を抜けて後続行に任意のDockerfile命令を
    注入されるおそれがあるため。
    """
    return value.replace("\n", " ").replace("\r", " ")


def _resolve_service_render_context(project_name: str, service: ServiceDefinition) -> dict[str, Any]:
    """image_or_build の値から、pre-builtイメージ参照かビルドが必要かを判定し、
    テンプレートに渡す値をあらかじめYAML安全な形にエスケープしたコンテキストを返す。

    "./" "../" "/" で始まる値はビルドコンテキストとみなし、
    それ以外はDockerイメージ参照（例: postgres:16-alpine）とみなす。
    """
    value = service.image_or_build
    is_build = value.startswith("./") or value.startswith("../") or value.startswith("/")

    return {
        # name はschemas.py側で英数字・ハイフン・アンダースコアのみに制限済みのため
        # YAMLのマッピングキー・ディレクトリ名として素のまま使用してよい。
        "name": service.name,
        "type": _sanitize_comment(service.type),
        "is_build": is_build,
        "image": None if is_build else _yaml_quote(value),
        "build_context": _yaml_quote(value) if is_build else None,
        "container_name": _yaml_quote(f"{project_name}-{service.name}"),
        "env": {_yaml_quote(k): _yaml_quote(v) for k, v in service.env.items()},
        "ports": [_yaml_quote(p) for p in service.ports],
    }


def build_iac_zip(submission: ClaudeJSONSubmission) -> bytes:
    """検証済みのClaudeJSONSubmissionから、docker-compose.ymlと
    各サービスのDockerfile（ビルドが必要なサービスのみ）を含むZIPをメモリ上に生成する。
    """
    services_ctx = [
        _resolve_service_render_context(submission.project_name, s) for s in submission.services
    ]

    compose_template = _env.get_template("docker-compose.yml.j2")
    compose_content = compose_template.render(services=services_ctx)

    dockerfile_template = _env.get_template("Dockerfile.j2")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("docker-compose.yml", compose_content)

        for service_ctx in services_ctx:
            if service_ctx["is_build"]:
                dockerfile_content = dockerfile_template.render(**service_ctx)
                zf.writestr(f"{service_ctx['name']}/Dockerfile", dockerfile_content)

        if submission.notes:
            zf.writestr("NOTES.md", f"# {submission.project_name}\n\n{submission.notes}\n")

    buffer.seek(0)
    return buffer.getvalue()
