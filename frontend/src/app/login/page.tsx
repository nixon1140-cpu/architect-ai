"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiRequest, saveAuthToken } from "@/lib/api";
import { useToast, ToastViewport } from "@/lib/toast";

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export default function LoginPage() {
  const router = useRouter();
  const { toast, showError, dismiss } = useToast();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;

    setSubmitting(true);
    try {
      const res = await apiRequest<TokenResponse>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), password }),
      });
      saveAuthToken(res.access_token);
      router.push("/");
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
              htmlFor="login-email"
            >
              メールアドレス
            </label>
            <input
              id="login-email"
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
              htmlFor="login-password"
            >
              パスワード
            </label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              className="rounded-sm border border-rule bg-well px-2.5 py-1.5 text-[13px] text-ink placeholder:text-muted/70 focus:border-accent-dim"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <button
            type="submit"
            className="mt-1 rounded-sm bg-accent px-3 py-2 text-[13px] font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-40"
            disabled={!email.trim() || !password || submitting}
          >
            {submitting ? "ログイン中..." : "ログイン"}
          </button>
        </form>

        <p className="mt-5 text-[13px] text-muted">
          アカウントをお持ちでない場合は{" "}
          <Link href="/register" className="text-accent hover:underline">
            新規登録
          </Link>
        </p>
      </div>

      <ToastViewport toast={toast} onDismiss={dismiss} />
    </div>
  );
}
