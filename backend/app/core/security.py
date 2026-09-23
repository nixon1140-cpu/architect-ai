"""パスワードハッシュ化・JWT発行/検証ユーティリティ。

passlibではなくbcryptライブラリを直接使用する（フェーズA1着手時、
passlib 1.7.4がbcrypt>=4.1の内部API変更に対応しておらず、ハッシュ化が
例外で失敗する既知の不具合を実機で確認したため。passlibは2020年から
更新が止まっており、根本対応として直接利用に切り替えた）。
仕様書 グループA・フェーズA1 参照。
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

# bcryptアルゴリズム自体の仕様上の制約（パスワードは72バイトまで）。
# これを超えるとbcrypt.hashpw()がValueErrorを送出するため、APIの
# 入力バリデーション側（schemas.UserRegisterRequest）で事前に弾く。
BCRYPT_MAX_PASSWORD_BYTES = 72

JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = 24 * 60  # 24時間

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise RuntimeError(
        "環境変数 JWT_SECRET_KEY が設定されていません。.env に設定してください。"
    )


def hash_password(password: str) -> str:
    """パスワードをbcryptでハッシュ化する。"""
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """平文パスワードがハッシュと一致するか検証する。"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str) -> tuple[str, datetime]:
    """ユーザーIDを主体とするJWTアクセストークンを発行する。

    戻り値はトークン本体と有効期限（UTC）のタプル。
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRES_MINUTES)
    payload: dict[str, Any] = {"sub": user_id, "exp": expires_at}
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, expires_at


class TokenError(Exception):
    """JWTの検証に失敗した場合（署名不正・期限切れ・形式不正等）に送出する。

    呼び出し側（main.pyの認証依存関数）で401 Unauthorizedへマッピングする。
    PyJWT固有の例外型をmain.py側に漏らさないためのラッパー。
    """


def decode_access_token(token: str) -> str:
    """JWTを検証し、主体（ユーザーID）を返す。"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError() from exc
    return payload["sub"]
