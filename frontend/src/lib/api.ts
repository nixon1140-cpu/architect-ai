// フェーズA3で導入した共通APIクライアント。
// 元々はpage.tsxに直接書かれていたAPI通信ヘルパー（ApiError/apiRequest/
// extractErrorMessage）を、ログイン・登録画面からも使えるようここへ切り出した。
// 認証（Authorizationヘッダの自動付与、トークン保管）もここに集約する。

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const AUTH_TOKEN_STORAGE_KEY = "architect-ai-auth-token";

export function saveAuthToken(token: string): void {
  try {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  } catch {
    // プライベートブラウジング等でlocalStorageが使えない場合は永続化を諦める。
  }
}

export function loadAuthToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function clearAuthToken(): void {
  try {
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    // ignore
  }
}

export class ApiError extends Error {
  status: number;
  // trueの場合、トークンを付与した認証済みリクエストが401で拒否されたことを
  // 示す（トークンが無効・期限切れ）。呼び出し元はログイン画面へ誘導すべき。
  // ログイン/登録エンドポイント自体が返す401（パスワード誤り等、トークン未付与）
  // とは区別する。
  isAuthExpired: boolean;

  constructor(status: number, message: string, isAuthExpired = false) {
    super(message);
    this.status = status;
    this.isAuthExpired = isAuthExpired;
  }
}

// バックエンドは大きく2種類のエラー形状を返す:
//  1) core/exceptions.py のカスタム例外 -> {"error": "...", "message": "...", ...}
//  2) FastAPI標準のHTTPException/バリデーションエラー -> {"detail": "..." または [{loc, msg, type}, ...]}
function extractErrorMessage(body: unknown): string {
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;

    if (typeof obj.message === "string") {
      // ClaudeJSONValidationErrorはerrors配列に詳細な異常箇所を含む。
      if (Array.isArray(obj.errors) && obj.errors.length > 0) {
        const details = obj.errors
          .map((e) => {
            if (e && typeof e === "object") {
              const err = e as Record<string, unknown>;
              const loc = Array.isArray(err.loc) ? err.loc.join(".") : "";
              return `${loc}: ${String(err.msg ?? "")}`;
            }
            return String(e);
          })
          .join(" / ");
        return `${obj.message} (${details})`;
      }
      return obj.message;
    }

    if (typeof obj.detail === "string") {
      return obj.detail;
    }

    if (Array.isArray(obj.detail)) {
      return obj.detail
        .map((e) => {
          if (e && typeof e === "object") {
            const err = e as Record<string, unknown>;
            const loc = Array.isArray(err.loc) ? err.loc.join(".") : "";
            return `${loc}: ${String(err.msg ?? "")}`;
          }
          return String(e);
        })
        .join(" / ");
    }
  }
  return "予期しないエラーが発生しました。";
}

export async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const token = loadAuthToken();
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {}),
    },
  });

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // 応答がJSONでない場合は無視してデフォルトメッセージを使う。
    }
    const isAuthExpired = res.status === 401 && Boolean(token);
    throw new ApiError(res.status, extractErrorMessage(body), isAuthExpired);
  }

  // 204 No Content（削除系エンドポイント等）はボディを持たないため、
  // res.json() を呼ばずに終える。既存のJSONを返すエンドポイントの
  // 挙動には影響しない。
  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

// /export はZIP（Blob）を返すため、上のapiRequest（JSON前提）とは別に用意する。
// 認証ヘッダの付与・エラー処理（401判定含む）は同じロジックを踏襲する。
export async function apiRequestBlob(
  path: string
): Promise<{ blob: Blob; filename: string }> {
  const token = loadAuthToken();
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    const isAuthExpired = res.status === 401 && Boolean(token);
    throw new ApiError(res.status, extractErrorMessage(body), isAuthExpired);
  }

  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match ? match[1] : "architect-ai-output.zip";
  return { blob, filename };
}
