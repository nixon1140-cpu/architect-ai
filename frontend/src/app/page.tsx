"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  apiRequest,
  apiRequestBlob,
  clearAuthToken,
  loadAuthToken,
} from "@/lib/api";
import { useToast, ToastViewport } from "@/lib/toast";

// ---------------------------------------------------------------------------
// 型定義（backend/app/schemas.py に対応）
// ---------------------------------------------------------------------------

type SessionState = "HEARING" | "PROPOSED" | "CLAUDE_REVIEW" | "COMPLETED";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface ProjectCreateResponse {
  project_id: string;
  session_id: string;
  title: string;
  current_state: SessionState;
}

interface ChatMessageResponse {
  reply: string;
  current_state: SessionState;
  claude_handoff_prompt: string | null;
}

interface ClaudeSubmissionResponse {
  status: string;
  current_state: SessionState;
}

interface ProjectListItem {
  project_id: string;
  title: string;
  created_at: string;
  session_id: string;
  current_state: SessionState;
  updated_at: string;
  claude_handoff_prompt: string | null;
  claude_json_content: string | null;
}

// ---------------------------------------------------------------------------
// アクティブセッションのlocalStorage永続化
// ---------------------------------------------------------------------------
// ArchitectAIは現時点でsession_id/project_idの永続化のみが必要で、it-news-app
// ほど状態管理が複雑ではないため、Zustandは新規導入せず素のlocalStorageで
// 実装する（ハイドレーション対策のuseState(false)+useEffectパターンは踏襲）。

const ACTIVE_SESSION_STORAGE_KEY = "architect-ai-active-session";

interface StoredActiveSession {
  projectId: string;
  sessionId: string;
}

function saveActiveSession(projectId: string, sessionId: string): void {
  try {
    localStorage.setItem(
      ACTIVE_SESSION_STORAGE_KEY,
      JSON.stringify({ projectId, sessionId })
    );
  } catch {
    // プライベートブラウジング等でlocalStorageが使えない場合は永続化を諦める。
  }
}

function clearActiveSession(): void {
  try {
    localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
  } catch {
    // ignore
  }
}

function loadActiveSession(): StoredActiveSession | null {
  try {
    const raw = localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredActiveSession>;
    if (typeof parsed.projectId === "string" && typeof parsed.sessionId === "string") {
      return { projectId: parsed.projectId, sessionId: parsed.sessionId };
    }
    return null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// 表示用の小部品（状態を持たず、既存のロジックには一切関与しない）
// ---------------------------------------------------------------------------

/** 計器盤の見出し。英字ラベルを等幅・大文字・広めの字送りで置く。 */
function SectionLabel({
  children,
  right,
}: {
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
        {children}
      </h2>
      {right}
    </div>
  );
}

/** ID・状態など、機械側の値を表示する行。値は等幅で読ませる。 */
function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
        {label}
      </span>
      <span className="break-all font-mono text-[11px] text-ink">{value}</span>
    </div>
  );
}

// このアプリの実体は4段の状態機械であり、順序そのものが利用者の必要とする
// 情報なので、装飾ではなく現在地を示す device としてヘッダーに常置する。
const PIPELINE: { id: SessionState; label: string }[] = [
  { id: "HEARING", label: "ヒアリング" },
  { id: "PROPOSED", label: "構成案" },
  { id: "CLAUDE_REVIEW", label: "レビュー" },
  { id: "COMPLETED", label: "確定" },
];

function PipelineRail({ state }: { state: SessionState | null }) {
  const activeIndex = state ? PIPELINE.findIndex((s) => s.id === state) : -1;

  return (
    <ol className="flex flex-wrap items-center gap-y-2">
      {PIPELINE.map((step, index) => {
        const done = activeIndex > index;
        const current = activeIndex === index;
        return (
          <li key={step.id} className="flex items-center">
            {index > 0 && (
              <span
                aria-hidden
                className={`mx-2 h-px w-5 ${
                  activeIndex >= index ? "bg-accent-dim" : "bg-rule"
                }`}
              />
            )}
            <span
              aria-current={current ? "step" : undefined}
              className="flex items-center gap-1.5"
            >
              <span
                aria-hidden
                className={`size-1.5 rounded-full ${
                  current
                    ? "bg-accent ring-4 ring-accent/15"
                    : done
                      ? "bg-accent-dim"
                      : "bg-rule-strong"
                }`}
              />
              <span
                className={`font-mono text-[10px] uppercase tracking-[0.14em] ${
                  current ? "text-accent" : done ? "text-ink" : "text-muted"
                }`}
              >
                {step.label}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// メイン画面（左:設定 / 中央:チャット / 右:出力・Claude連携 の1画面SPA）
// ---------------------------------------------------------------------------

export default function Home() {
  const router = useRouter();
  const { toast, showError, showInfo, dismiss } = useToast();

  // --- ハイドレーション対策: it-news-appのarticle-card.tsxと同じパターン。
  // SSR/初回クライアント描画ではlocalStorageの内容を反映せず、マウント後に
  // 初めて復元処理を行う。
  const [mounted, setMounted] = useState(false);

  // --- プロジェクト/セッション状態 -----------------------------------------
  const [projectTitle, setProjectTitle] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [projectDisplayTitle, setProjectDisplayTitle] = useState<string | null>(null);
  const [currentState, setCurrentState] = useState<SessionState | null>(null);

  // --- プロジェクト一覧状態 ---------------------------------------------------
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [restoringSession, setRestoringSession] = useState(false);
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(
    null
  );

  // --- チャット状態 ---------------------------------------------------------
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [sendingChat, setSendingChat] = useState(false);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  // --- Claude連携状態 --------------------------------------------------------
  const [claudeHandoffPrompt, setClaudeHandoffPrompt] = useState<string | null>(null);
  const [claudeRawText, setClaudeRawText] = useState("");
  const [submittingClaudeJson, setSubmittingClaudeJson] = useState(false);

  // --- 確定済みJSON編集状態（COMPLETED状態向け） -------------------------------
  const [claudeJsonEditText, setClaudeJsonEditText] = useState("");
  const [savingClaudeJsonEdit, setSavingClaudeJsonEdit] = useState(false);

  // --- ZIPダウンロード状態 ----------------------------------------------------
  const [exporting, setExporting] = useState(false);
  const [exportFormat, setExportFormat] = useState<"docker-compose" | "terraform">(
    "docker-compose"
  );

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // --- プロジェクト一覧取得 ---------------------------------------------------
  async function fetchProjects(): Promise<ProjectListItem[]> {
    setLoadingProjects(true);
    try {
      const list = await apiRequest<ProjectListItem[]>("/api/v1/projects");
      setProjects(list);
      return list;
    } catch (err) {
      showError(err);
      return [];
    } finally {
      setLoadingProjects(false);
    }
  }

  // --- 一覧項目からプロジェクトを選択（新規作成・復元・切り替えの共通処理） -----
  function applyProjectSelection(item: ProjectListItem) {
    setProjectId(item.project_id);
    setSessionId(item.session_id);
    setProjectDisplayTitle(item.title);
    setCurrentState(item.current_state);
    setMessages([]);
    setChatInput("");
    // CLAUDE_REVIEW状態のプロジェクトのみ、既存messages履歴から導出された
    // ハンドオフプロンプトがバックエンドから返る。それ以外はnull。
    setClaudeHandoffPrompt(item.claude_handoff_prompt);
    setClaudeRawText("");
    // COMPLETED状態のプロジェクトのみ、既存messages履歴から導出された
    // 確定済みJSON（整形済み）がバックエンドから返る。それ以外はnull。
    setClaudeJsonEditText(item.claude_json_content ?? "");
    saveActiveSession(item.project_id, item.session_id);
  }

  // --- マウント後、未ログインならログイン画面へ、そうでなければ一覧取得と
  // 保存済みセッションの復元を行う ---------------------------------------------
  useEffect(() => {
    setMounted(true);

    if (!loadAuthToken()) {
      router.push("/login");
      return;
    }

    (async () => {
      const list = await fetchProjects();
      const saved = loadActiveSession();
      if (!saved) return;

      setRestoringSession(true);
      const match = list.find((p) => p.session_id === saved.sessionId);
      if (match) {
        applyProjectSelection(match);
      } else {
        // GET /api/v1/projects の一覧に存在しない = バックエンド側で
        // 該当セッションがもう存在しないのと同義（既存のsubmit_claude_json等の
        // 404ハンドリングと同じ考え方で、保存値をクリアして初期画面へ戻す）。
        clearActiveSession();
        showError(
          new Error(
            "保存されていたプロジェクトが見つかりませんでした。新規作成画面を表示します。"
          )
        );
      }
      setRestoringSession(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- プロジェクト新規作成 ---------------------------------------------------
  async function handleCreateProject() {
    if (!projectTitle.trim()) return;
    setCreatingProject(true);
    try {
      const res = await apiRequest<ProjectCreateResponse>("/api/v1/projects", {
        method: "POST",
        body: JSON.stringify({ title: projectTitle.trim() }),
      });
      setProjectId(res.project_id);
      setSessionId(res.session_id);
      setProjectDisplayTitle(res.title);
      setCurrentState(res.current_state);
      setMessages([]);
      setClaudeHandoffPrompt(null);
      setClaudeRawText("");
      setClaudeJsonEditText("");
      saveActiveSession(res.project_id, res.session_id);
      showInfo("プロジェクトを作成しました。ヒアリングを開始してください。");
      await fetchProjects();
    } catch (err) {
      showError(err);
    } finally {
      setCreatingProject(false);
    }
  }

  function handleResetProject() {
    setProjectId(null);
    setSessionId(null);
    setProjectDisplayTitle(null);
    setCurrentState(null);
    setMessages([]);
    setChatInput("");
    setClaudeHandoffPrompt(null);
    setClaudeRawText("");
    setClaudeJsonEditText("");
    setProjectTitle("");
    clearActiveSession();
  }

  // --- ログアウト -------------------------------------------------------------
  function handleLogout() {
    clearAuthToken();
    clearActiveSession();
    router.push("/login");
  }

  // --- プロジェクト削除 -------------------------------------------------------
  async function handleDeleteProject(item: ProjectListItem) {
    const confirmed = window.confirm(
      `プロジェクト「${item.title}」を削除します。この操作は取り消せません。よろしいですか？`
    );
    if (!confirmed) return;

    setDeletingProjectId(item.project_id);
    try {
      await apiRequest<void>(`/api/v1/projects/${item.project_id}`, {
        method: "DELETE",
      });
      showInfo(`プロジェクト「${item.title}」を削除しました。`);
      // 削除対象が現在表示中のプロジェクトだった場合は、初期画面へ戻す。
      if (item.project_id === projectId) {
        handleResetProject();
      }
      await fetchProjects();
    } catch (err) {
      showError(err);
    } finally {
      setDeletingProjectId(null);
    }
  }

  // --- チャット送信（通常ヒアリング / フェーズ進行の両方を担う） -----------------
  async function handleSendChat(requestProposal: boolean) {
    if (!sessionId) return;

    const defaultProposalMessage =
      currentState === "HEARING"
        ? "これまでの内容をもとに構成案を作成してください。"
        : "この内容で確定し、Claudeへのハンドオフプロンプトを作成してください。";

    const messageToSend = chatInput.trim() || (requestProposal ? defaultProposalMessage : "");
    if (!messageToSend) return;

    setSendingChat(true);
    setMessages((prev) => [...prev, { role: "user", content: messageToSend }]);
    setChatInput("");

    try {
      const query = requestProposal ? "?request_proposal=true" : "";
      const res = await apiRequest<ChatMessageResponse>(
        `/api/v1/sessions/${sessionId}/chat${query}`,
        {
          method: "POST",
          body: JSON.stringify({ message: messageToSend }),
        }
      );
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply }]);
      setCurrentState(res.current_state);
      if (res.claude_handoff_prompt) {
        setClaudeHandoffPrompt(res.claude_handoff_prompt);
      }
    } catch (err) {
      showError(err);
    } finally {
      setSendingChat(false);
    }
  }

  // --- Claude Web出力の提出 ---------------------------------------------------
  async function handleSubmitClaudeJson() {
    if (!sessionId || !claudeRawText.trim()) return;
    setSubmittingClaudeJson(true);
    try {
      const res = await apiRequest<ClaudeSubmissionResponse>(
        `/api/v1/sessions/${sessionId}/submit_claude_json`,
        {
          method: "POST",
          body: JSON.stringify({ raw_text: claudeRawText }),
        }
      );
      setCurrentState(res.current_state);
      showInfo("Claude Webの出力を確定しました。ZIPをダウンロードできます。");
      // 確定済みJSON編集用テキストエリアへ、バックエンドが整形済みで
      // 返す最新の内容を反映する（自前でフェンス除去等はしない）。
      const list = await fetchProjects();
      const match = list.find((p) => p.session_id === sessionId);
      setClaudeJsonEditText(match?.claude_json_content ?? "");
    } catch (err) {
      showError(err);
    } finally {
      setSubmittingClaudeJson(false);
    }
  }

  // --- 確定済みJSONの編集・再生成（HITLバイパス、意図した設計） -------------------
  async function handleEditClaudeJson() {
    if (!sessionId || !claudeJsonEditText.trim()) return;
    setSavingClaudeJsonEdit(true);
    try {
      await apiRequest<ClaudeSubmissionResponse>(
        `/api/v1/sessions/${sessionId}/edit_claude_json`,
        {
          method: "PUT",
          body: JSON.stringify({ raw_text: claudeJsonEditText }),
        }
      );
      showInfo("編集内容で再生成しました。ZIPダウンロードに反映されています。");
      const list = await fetchProjects();
      const match = list.find((p) => p.session_id === sessionId);
      setClaudeJsonEditText(match?.claude_json_content ?? claudeJsonEditText);
    } catch (err) {
      showError(err);
    } finally {
      setSavingClaudeJsonEdit(false);
    }
  }

  // --- クリップボードコピー ----------------------------------------------------
  async function handleCopyHandoffPrompt() {
    if (!claudeHandoffPrompt) return;
    try {
      await navigator.clipboard.writeText(claudeHandoffPrompt);
      showInfo("クリップボードにコピーしました。");
    } catch {
      showError(new Error("クリップボードへのコピーに失敗しました。"));
    }
  }

  // --- ZIPダウンロード ---------------------------------------------------------
  async function handleExport() {
    if (!sessionId) return;
    setExporting(true);
    try {
      const { blob, filename } = await apiRequestBlob(
        `/api/v1/sessions/${sessionId}/export?output_format=${exportFormat}`
      );

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showError(err);
    } finally {
      setExporting(false);
    }
  }

  const chatEnabled = currentState === "HEARING";
  const proposalButtonLabel =
    currentState === "HEARING" ? "構成案を作成してもらう" : "Claudeへのハンドオフを作成";
  const proposalButtonEnabled = currentState === "HEARING" || currentState === "PROPOSED";

  return (
    <div className="flex min-h-screen flex-col bg-ground text-ink lg:h-screen">
      <header className="flex flex-col gap-3 border-b border-rule bg-panel px-5 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[15px] font-semibold tracking-tight text-ink">
            ArchitectAI
          </h1>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
            system design console
          </span>
        </div>
        <div className="flex items-center gap-4">
          <PipelineRail state={currentState} />
          <button
            type="button"
            className="rounded-sm border border-rule-strong px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted transition-colors hover:border-accent-dim hover:text-accent"
            onClick={handleLogout}
          >
            ログアウト
          </button>
        </div>
      </header>

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row lg:overflow-hidden">
        {/* 左パネル: 設定 */}
        <aside className="flex shrink-0 flex-col gap-5 border-b border-rule bg-panel p-5 lg:w-[280px] lg:border-b-0 lg:border-r lg:overflow-y-auto">
          <SectionLabel>設定</SectionLabel>

          {!sessionId ? (
            <div className="flex flex-col gap-2">
              <label
                className="text-[13px] text-ink"
                htmlFor="project-title"
              >
                新規プロジェクト名
              </label>
              <input
                id="project-title"
                className="rounded-sm border border-rule bg-well px-2.5 py-1.5 text-[13px] text-ink placeholder:text-muted/70 focus:border-accent-dim"
                placeholder="例: ECサイト構築プロジェクト"
                value={projectTitle}
                onChange={(e) => setProjectTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreateProject();
                }}
              />
              <button
                className="rounded-sm bg-accent px-3 py-2 text-[13px] font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-40"
                disabled={!projectTitle.trim() || creatingProject}
                onClick={handleCreateProject}
              >
                {creatingProject ? "作成中..." : "プロジェクトを作成"}
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-3.5">
              <div className="flex flex-col gap-0.5">
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
                  プロジェクト名
                </span>
                <span className="text-[14px] font-semibold text-ink">
                  {projectDisplayTitle}
                </span>
              </div>
              <MetaRow label="project_id" value={projectId} />
              <MetaRow label="session_id" value={sessionId} />
              <MetaRow
                label="状態"
                value={<span className="text-accent">{currentState}</span>}
              />
              <button
                className="mt-1 rounded-sm border border-rule-strong px-3 py-2 text-[13px] text-ink transition-colors hover:border-accent-dim hover:text-accent"
                onClick={handleResetProject}
              >
                新しいプロジェクトを作成
              </button>
            </div>
          )}

          {mounted && (
            <div className="flex flex-col gap-2 border-t border-rule pt-5">
              <SectionLabel
                right={
                  restoringSession ? (
                    <span className="font-mono text-[10px] text-accent">
                      復元中...
                    </span>
                  ) : undefined
                }
              >
                プロジェクト一覧
              </SectionLabel>
              {loadingProjects && (
                <p className="font-mono text-[11px] text-muted">読み込み中...</p>
              )}
              {!loadingProjects && projects.length === 0 && (
                <p className="text-[12px] text-muted">
                  まだプロジェクトがありません。
                </p>
              )}
              <ul className="flex flex-col">
                {projects.map((p) => {
                  const selected = p.session_id === sessionId;
                  const deleting = deletingProjectId === p.project_id;
                  return (
                    <li
                      key={p.project_id}
                      className="flex items-stretch border-b border-rule/60"
                    >
                      <button
                        className={`min-w-0 flex-1 px-2 py-2.5 text-left transition-colors ${
                          selected
                            ? "bg-well text-ink"
                            : "text-muted hover:bg-well hover:text-ink"
                        }`}
                        onClick={() => applyProjectSelection(p)}
                      >
                        <div
                          className={`truncate text-[13px] ${
                            selected ? "font-semibold text-accent" : ""
                          }`}
                        >
                          {p.title}
                        </div>
                        <div className="mt-1 flex items-center justify-between gap-2 font-mono text-[10px] tracking-[0.08em] text-muted">
                          <span>{p.current_state}</span>
                          <span className="tabular-nums">
                            {new Date(p.updated_at).toLocaleString("ja-JP")}
                          </span>
                        </div>
                      </button>
                      <button
                        type="button"
                        className="shrink-0 px-2.5 font-mono text-[12px] text-muted transition-colors hover:text-danger disabled:opacity-40 disabled:hover:text-muted"
                        onClick={() => handleDeleteProject(p)}
                        disabled={deleting}
                        aria-label={`プロジェクト「${p.title}」を削除`}
                        title="削除"
                      >
                        {deleting ? "…" : "✕"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </aside>

        {/* 中央パネル: チャット */}
        <main className="relative flex min-h-[55vh] flex-1 flex-col lg:min-h-0 lg:overflow-hidden">
          <div className="border-b border-rule bg-panel px-5 py-2.5">
            <SectionLabel
              right={
                sessionId ? (
                  <span className="font-mono text-[10px] tabular-nums text-muted">
                    {messages.length} 件
                  </span>
                ) : undefined
              }
            >
              チャット
            </SectionLabel>
          </div>
          {!sessionId ? (
            <div className="flex flex-1 items-center justify-center">
              <p className="text-[13px] text-muted">
                左のパネルからプロジェクトを作成してください。
              </p>
            </div>
          ) : (
            <>
              <div className="flex-1 overflow-y-auto p-5">
                {messages.length === 0 && (
                  <p className="text-[13px] text-muted">
                    どのようなシステムを作りたいか、要件をチャットで伝えてください。
                  </p>
                )}
                <div className="flex flex-col gap-3">
                  {messages.map((m, i) => (
                    <div
                      key={i}
                      className={`whitespace-pre-wrap rounded-sm px-3.5 py-2.5 text-[13px] leading-relaxed ${
                        m.role === "user"
                          ? "max-w-[85%] self-end bg-accent font-medium text-ground"
                          : "max-w-[92%] self-start border border-rule bg-well text-ink"
                      }`}
                    >
                      {m.content}
                    </div>
                  ))}
                  <div ref={chatEndRef} />
                </div>
              </div>

              <div className="flex flex-col gap-2 border-t border-rule bg-panel p-4">
                <textarea
                  className="w-full resize-none rounded-sm border border-rule bg-well px-2.5 py-2 text-[13px] text-ink placeholder:text-muted/70 focus:border-accent-dim disabled:opacity-50"
                  rows={2}
                  placeholder={
                    chatEnabled
                      ? "メッセージを入力..."
                      : "現在の状態ではヒアリングチャットは利用できません。"
                  }
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  disabled={!chatEnabled && !proposalButtonEnabled}
                />
                <div className="flex flex-wrap justify-end gap-2">
                  <button
                    className="rounded-sm border border-rule-strong px-3 py-2 text-[13px] text-ink transition-colors hover:border-accent-dim hover:text-accent disabled:opacity-40 disabled:hover:border-rule-strong disabled:hover:text-ink"
                    disabled={!chatEnabled || !chatInput.trim() || sendingChat}
                    onClick={() => handleSendChat(false)}
                  >
                    {sendingChat ? "送信中..." : "送信"}
                  </button>
                  <button
                    className="rounded-sm bg-accent px-3 py-2 text-[13px] font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-40"
                    disabled={!proposalButtonEnabled || sendingChat}
                    onClick={() => handleSendChat(true)}
                  >
                    {sendingChat ? "処理中..." : proposalButtonLabel}
                  </button>
                </div>
              </div>
            </>
          )}
        </main>

        {/* 右パネル: 出力 / Claude連携 */}
        <aside className="flex shrink-0 flex-col gap-4 border-t border-rule bg-panel p-5 lg:w-[400px] lg:border-l lg:border-t-0 lg:overflow-y-auto">
          <SectionLabel>出力 / Claude連携</SectionLabel>

          {!sessionId && (
            <p className="text-[13px] leading-relaxed text-muted">
              プロジェクトを作成すると、ここに進行状況が表示されます。
            </p>
          )}

          {sessionId && currentState === "HEARING" && (
            <p className="text-[13px] leading-relaxed text-muted">
              ヒアリング中です。十分に要件を伝えたら、中央下部の「構成案を作成してもらう」を押してください。
            </p>
          )}

          {sessionId && currentState === "PROPOSED" && (
            <p className="text-[13px] leading-relaxed text-muted">
              構成案が作成されました。内容を確認し、問題なければ中央下部の「Claudeへのハンドオフを作成」を押してください。
            </p>
          )}

          {sessionId && currentState === "CLAUDE_REVIEW" && (
            <div className="flex flex-col gap-5">
              <div className="flex flex-col gap-1.5">
                <SectionLabel
                  right={
                    <button
                      className="rounded-sm border border-rule-strong px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink transition-colors hover:border-accent-dim hover:text-accent"
                      onClick={handleCopyHandoffPrompt}
                    >
                      コピー
                    </button>
                  }
                >
                  Claude Web貼り付け用プロンプト
                </SectionLabel>
                <textarea
                  className="h-40 w-full resize-none rounded-sm border border-rule bg-well px-2.5 py-2 font-mono text-[11px] leading-relaxed text-muted"
                  readOnly
                  value={claudeHandoffPrompt ?? ""}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
                  Claude Webの出力を貼り付け
                </label>
                <textarea
                  className="h-40 w-full resize-none rounded-sm border border-rule bg-well px-2.5 py-2 font-mono text-[11px] leading-relaxed text-ink placeholder:font-sans placeholder:text-muted/70 focus:border-accent-dim"
                  placeholder="Claude Webが出力したJSON（```json フェンス付きでも可）を貼り付けてください。"
                  value={claudeRawText}
                  onChange={(e) => setClaudeRawText(e.target.value)}
                />
              </div>

              <button
                className="rounded-sm bg-accent px-3 py-2 text-[13px] font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-40"
                disabled={!claudeRawText.trim() || submittingClaudeJson}
                onClick={handleSubmitClaudeJson}
              >
                {submittingClaudeJson ? "送信中..." : "この内容で確定する（submit_claude_json）"}
              </button>
            </div>
          )}

          {sessionId && currentState === "COMPLETED" && (
            <div className="flex flex-col gap-4">
              <p className="text-[13px] leading-relaxed text-muted">
                構成が確定しました。初期インフラコード一式をZIPでダウンロードできます。
                内容を直接編集して再生成することもできます。
              </p>

              <div className="flex flex-col gap-2">
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
                  出力形式
                </span>
                <div className="flex gap-4">
                  {(
                    [
                      { value: "docker-compose", label: "Docker Compose" },
                      { value: "terraform", label: "Terraform" },
                    ] as const
                  ).map((option) => (
                    <label
                      key={option.value}
                      className="flex cursor-pointer items-center gap-1.5 text-[13px] text-ink"
                    >
                      <input
                        type="radio"
                        name="export-format"
                        value={option.value}
                        checked={exportFormat === option.value}
                        onChange={() => setExportFormat(option.value)}
                        className="accent-accent"
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              </div>

              <button
                className="flex items-center justify-center gap-2 rounded-sm border border-ok/50 bg-ok/10 px-3 py-2 text-[13px] font-semibold text-ok transition-colors hover:bg-ok/20 disabled:opacity-40"
                disabled={exporting}
                onClick={handleExport}
              >
                {exporting ? "生成中..." : "ZIPをダウンロード"}
              </button>

              <div className="flex flex-col gap-1.5 border-t border-rule pt-4">
                <label className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
                  確定済みJSON（編集可能）
                </label>
                <textarea
                  className="h-64 w-full resize-none rounded-sm border border-rule bg-well px-2.5 py-2 font-mono text-[11px] leading-relaxed text-ink focus:border-accent-dim"
                  spellCheck={false}
                  value={claudeJsonEditText}
                  onChange={(e) => setClaudeJsonEditText(e.target.value)}
                />
                <button
                  className="mt-1 w-full rounded-sm bg-accent px-3 py-2 text-[13px] font-semibold text-ground transition-opacity hover:opacity-90 disabled:opacity-40"
                  disabled={!claudeJsonEditText.trim() || savingClaudeJsonEdit}
                  onClick={handleEditClaudeJson}
                >
                  {savingClaudeJsonEdit ? "再生成中..." : "この内容で再生成する"}
                </button>
              </div>
            </div>
          )}
        </aside>
      </div>

      {/* トースト（エラー等は必ずここに表示する。コンソールへの握りつぶしは行わない） */}
      <ToastViewport toast={toast} onDismiss={dismiss} />
    </div>
  );
}
