"use client";

// フェーズA3で導入した共通トースト。元々page.tsxに直接書かれていたuseToast
// フック・表示部を、ログイン・登録画面からも使えるようここへ切り出した。
// あわせて、認証済みリクエストがトークン無効・期限切れ（401）で拒否された
// 場合に、トーストで理由を表示した上でログイン画面へ誘導する処理をここに集約する
// （コンソールへの握りつぶし禁止、既存の作法を踏襲）。

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, clearAuthToken } from "@/lib/api";

interface ToastState {
  kind: "error" | "info";
  text: string;
}

export function useToast() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const router = useRouter();

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(timer);
  }, [toast]);

  const showError = (err: unknown) => {
    const text =
      err instanceof ApiError
        ? `[HTTP ${err.status}] ${err.message}`
        : err instanceof Error
          ? err.message
          : "予期しないエラーが発生しました。";
    setToast({ kind: "error", text });

    if (err instanceof ApiError && err.isAuthExpired) {
      clearAuthToken();
      // すぐに遷移すると、遷移先(/login)はトースト状態を引き継がない別コンポーネント
      // インスタンスのため、上で表示したトーストがユーザーの目に触れる前に消えてしまう。
      // 「エラーを画面表示した上でリダイレクト」という要件を満たすため、
      // 表示が見える時間だけ遅らせてから遷移する。
      setTimeout(() => router.push("/login"), 1500);
    }
  };

  const showInfo = (text: string) => setToast({ kind: "info", text });

  return { toast, showError, showInfo, dismiss: () => setToast(null) };
}

export function ToastViewport({
  toast,
  onDismiss,
}: {
  toast: { kind: "error" | "info"; text: string } | null;
  onDismiss: () => void;
}) {
  if (!toast) return null;

  return (
    <div
      className={`toast-enter fixed bottom-5 right-5 z-50 max-w-sm rounded-sm border-l-2 bg-panel px-4 py-3 shadow-2xl shadow-black/60 ${
        toast.kind === "error" ? "border-danger" : "border-accent"
      }`}
      role="alert"
    >
      <div className="flex items-start gap-3">
        <div className="flex flex-1 flex-col gap-1">
          <span
            className={`font-mono text-[10px] uppercase tracking-[0.18em] ${
              toast.kind === "error" ? "text-danger" : "text-accent"
            }`}
          >
            {toast.kind === "error" ? "error" : "info"}
          </span>
          <span className="text-[13px] leading-relaxed text-ink">{toast.text}</span>
        </div>
        <button
          className="rounded-sm px-1 text-[12px] text-muted transition-colors hover:text-ink"
          onClick={onDismiss}
          aria-label="通知を閉じる"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
