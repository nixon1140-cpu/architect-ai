"""Claude Web由来の確定JSONから、インフラ構成一式をメモリ上でZIP化する。

Jinja2テンプレート（app/templates/*.j2）に変数を流し込み、
io.BytesIO + zipfile.ZipFile によりディスクI/Oなしでメモリ上にZIPを構築する。
仕様書 9.2章 参照。出力形式はDocker Compose（既定）とTerraformの2種類から
選択できる（フェーズ グループB）。python-hcl2・CDKTFはいずれも今回の用途に
不適と判断し不採用（詳細はコミットメッセージ・報告を参照）。既存の
docker-compose.yml.j2と同じ「Jinja2テンプレートで対象フォーマットの構文を
直接出力する」方式をTerraformでも踏襲し、main.tf.j2を追加する形にした。

セキュリティ上の注意:
    schemas.ServiceDefinition.name は英数字・ハイフン・アンダースコアのみに制限
    済みだが、image_or_build・env・ports・notes 等の自由入力フィールドは
    コロンや改行などのYAML/HCL特殊文字を含み得る。これらをそのままテンプレートへ
    渡すと出力構造を破壊する（インジェクション）ため、_yaml_quote() /
    _hcl_quote() でそれぞれの形式の文字列リテラルとして安全にエスケープして
    から渡す。
"""

import io
import zipfile
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas import ClaudeJSONSubmission, ServiceDefinition

IacFormat = Literal["docker-compose", "terraform"]

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


def _hcl_quote(value: str) -> str:
    """Terraform(HCL2)のダブルクォート付き文字列リテラルとして安全な形に
    エスケープする。

    HCL2の文字列エスケープ規則はYAMLのそれとほぼ同じ（バックスラッシュ・
    ダブルクォート・改行のエスケープ）だが、将来どちらかの仕様が変わっても
    互いに影響しないよう、_yaml_quote()とは独立した関数として保持する。
    "${...}" はHCLの補間構文のため、そのまま出力すると意図しない式展開を
    引き起こす。$を$$へエスケープして補間を無効化する。
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def _parse_port_mapping(port: str) -> tuple[str, str]:
    """"external:internal" 形式のポート文字列を (external, internal) に分割する。

    コロンが無い、または数値でない場合も落ちないよう、パースできない場合は
    元の文字列を external/internal 両方に使うフォールバックとする
    （Terraform側でエラーとして顕在化させ、ZIP生成自体は失敗させない）。
    """
    parts = port.split(":", 1)
    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        return parts[0].strip(), parts[1].strip()
    return port, port


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


def _resolve_service_render_context_terraform(
    project_name: str, service: ServiceDefinition
) -> dict[str, Any]:
    """Terraform（kreuzwerker/dockerプロバイダ）向けのレンダリングコンテキストを
    返す。_resolve_service_render_context()のHCL版で、判定ロジック
    （ビルド要否・pre-builtイメージ判定）は同一だが、エスケープには
    _hcl_quote()を用いる。

    env は "KEY=VALUE" という1つの文字列をまるごとHCL文字列として
    エスケープしてからリスト化する（キー・値を個別にエスケープして後で
    連結すると、"="自体はデータとして安全だが、連結順序の誤りによる
    構造混入の余地を作らないため）。ports は "external:internal" 形式を
    分割し、Terraformの ports { internal = ... external = ... } ブロックへ
    渡す。
    """
    value = service.image_or_build
    is_build = value.startswith("./") or value.startswith("../") or value.startswith("/")

    env_entries = [_hcl_quote(f"{k}={v}") for k, v in service.env.items()]
    port_entries = [
        {"external": external, "internal": internal}
        for external, internal in (_parse_port_mapping(p) for p in service.ports)
    ]

    return {
        "name": service.name,
        "type": _sanitize_comment(service.type),
        "is_build": is_build,
        "image_ref": _hcl_quote(f"{project_name}-{service.name}" if is_build else value),
        "build_context": _hcl_quote(value) if is_build else None,
        "container_name": _hcl_quote(f"{project_name}-{service.name}"),
        "env_entries": env_entries,
        "ports": port_entries,
    }


def build_iac_zip(
    submission: ClaudeJSONSubmission, output_format: IacFormat = "docker-compose"
) -> bytes:
    """検証済みのClaudeJSONSubmissionから、インフラ構成一式
    （docker-compose.yml または main.tf）と各サービスのDockerfile
    （ビルドが必要なサービスのみ）を含むZIPをメモリ上に生成する。

    output_format="docker-compose"（既定）: docker-compose.yml を生成。
    output_format="terraform": main.tf（kreuzwerker/dockerプロバイダ）を生成。
    いずれの形式でも、ビルドが必要なサービスのDockerfileは共通で同梱する
    （Terraform側の docker_image リソースも build.context にDockerfileが
    存在することを前提とするため）。
    """
    dockerfile_template = _env.get_template("Dockerfile.j2")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if output_format == "terraform":
            services_ctx = [
                _resolve_service_render_context_terraform(submission.project_name, s)
                for s in submission.services
            ]
            main_tf_template = _env.get_template("main.tf.j2")
            zf.writestr("main.tf", main_tf_template.render(services=services_ctx))
        else:
            services_ctx = [
                _resolve_service_render_context(submission.project_name, s)
                for s in submission.services
            ]
            compose_template = _env.get_template("docker-compose.yml.j2")
            zf.writestr("docker-compose.yml", compose_template.render(services=services_ctx))

        for service_ctx in services_ctx:
            if service_ctx["is_build"]:
                dockerfile_content = dockerfile_template.render(**service_ctx)
                zf.writestr(f"{service_ctx['name']}/Dockerfile", dockerfile_content)

        if submission.notes:
            zf.writestr("NOTES.md", f"# {submission.project_name}\n\n{submission.notes}\n")

    buffer.seek(0)
    return buffer.getvalue()
