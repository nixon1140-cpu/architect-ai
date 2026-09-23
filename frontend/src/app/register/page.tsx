"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiRequest } from "@/lib/api";
import { useToast, ToastViewport } from "@/lib/toast";

interface UserRegisterResponse {
  user_id: string;
  email: string;
}

export default function RegisterPage() {
  const router = useRouter();
  const { toast, showError, showInfo, dismiss } = useToast();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;

    setSubmitting(true);
    try {
      await apiRequest<UserRegisterResponse>("/api/v1/auth/register", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), password }),
      });
      // 登録のみでトークンは発行されない（バックエンドの設計判断）ため、
      // ログイン画面へ誘導する。即座に遷移するとトーストが表示される前に
      // 画面が切り替わってしまうため、少し遅らせる。
      showInfo("登録が完了しました。ログインしてください。");
      setTimeout(() => router.push("/login"), 1200);
    } catch (err) {
      showError(err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-ground px-5 text-ink">
      <div className="w-full max-w-sm rounded-sm border border-rule bg-panel p-6">
        <div className="mb-6 flex items-baseline gap-3">
          <h1 className="text-[15px] font-semibold tracking-tight text-ink">
            ArchitectAI
          </h1>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
            system design console
          </span>
        </div>

        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-2">
            <label
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted"
              htmlFor="register-email"
            >
              メールアドレス
            </label>
            <input
              id="register-email"
              type="email"
              autoComplete="email"
              required
              className="rounded-sm border border-rule bg-well px-2.5 py-1.5 text-[13px] text-ink placeholder:text-muted/70 focus:border-accent-dim"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-2">
            <label
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted"
              htmlFor="register-password"
            >
              パスワード
            </label>
            <input
              id="register-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              className="rounded-sm border border-rule bg-well px-2.5 py-1.5 text-[13px] text-ink placeholder:text-muted/70 focus:border-accent-dim"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <span className="text-[11px] leading-relaxed text-muted">
              8文字以上72バイト以内（日本語等のマルチバイト文字は1文字が複数バイトになります）。
            </span>
          </div>

          <button
            type="submit"
            className="mt-1 rounded-sm bg-accent px-3 py-2 text-[13px] font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-40"
            disabled={!email.trim() || !password || submitting}
          >
            {submitting ? "登録中..." : "新規登録"}
          </button>
        </form>

        <p className="mt-5 text-[13px] text-muted">
          既にアカウントをお持ちの場合は{" "}
          <Link href="/login" className="text-accent hover:underline">
            ログイン
          </Link>
        </p>
      </div>

      <ToastViewport toast={toast} onDismiss={dismiss} />
    </div>
  );
}
