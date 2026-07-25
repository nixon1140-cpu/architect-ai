"""構造化ログ（JSON形式）設定。

各ログエントリは timestamp / level / logger_name / message / session_id（該当する場合）を
最低限含むJSONとして標準出力に出力する。例外発生時は exc_info=True でスタックトレースを含める。
仕様書 7章 参照。
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "message": record.getMessage(),
        }

        session_id = getattr(record, "session_id", None)
        if session_id is not None:
            payload["session_id"] = str(session_id)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    """アプリケーション起動時に一度だけ呼び出し、ルートロガーへJSON出力ハンドラを設定する。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
