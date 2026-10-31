"use client";

import { createContext, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { ControlPlaneEventBridge } from "@/components/Providers";

type PanelSession = {
  required: boolean;
  logout: () => Promise<void>;
};

const PanelSessionContext = createContext<PanelSession>({
  required: false,
  logout: async () => {},
});

export function usePanelSession(): PanelSession {
  return useContext(PanelSessionContext);
}

async function readSession(): Promise<{ required: boolean; ok: boolean }> {
  const response = await fetch("/api/panel-session", { cache: "no-store" });
  if (!response.ok) return { required: true, ok: false };
  return response.json() as Promise<{ required: boolean; ok: boolean }>;
}

export function PanelLock({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"loading" | "locked" | "open">("loading");
  const [required, setRequired] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    readSession()
      .then((session) => {
        if (cancelled) return;
        setRequired(session.required);
        setState(session.ok ? "open" : "locked");
      })
      .catch(() => {
        if (!cancelled) setState("locked");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function logout() {
    await fetch("/api/panel-session", { method: "DELETE" });
    window.location.assign("/overview");
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/panel-session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        setError("密码不正确");
        return;
      }
      setPassword("");
      setState("open");
      setRequired(true);
    } catch {
      setError("无法连接控制面板");
    } finally {
      setSubmitting(false);
    }
  }

  if (state !== "open") {
    return (
      <main className="grid min-h-screen place-items-center bg-[#F4F5F6] px-4">
        <form onSubmit={onSubmit} className="w-full max-w-sm rounded-md border border-[#CFD5DA] bg-white p-6 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-[#B54708]">GIDS</p>
          <h1 className="mt-2 text-xl font-semibold text-[#1F2933]">控制中心</h1>
          <p className="mt-1 text-sm text-[#52606F]">{state === "loading" ? "正在确认登录状态" : "请输入控制面板密码"}</p>
          {state === "locked" ? (
            <>
              <label className="mt-5 block text-sm font-medium text-[#1F2933]" htmlFor="panel-password">密码</label>
              <input
                id="panel-password"
                type="password"
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="mt-1 w-full rounded-md border border-[#CFD5DA] px-3 py-2 text-sm text-[#1F2933] outline-none focus:border-[#175CD3]"
              />
              {error ? <p className="mt-2 text-sm text-[#B42318]">{error}</p> : null}
              <button type="submit" disabled={submitting || !password} className="cc-button-primary mt-4 w-full">
                {submitting ? "正在进入" : "进入"}
              </button>
            </>
          ) : null}
        </form>
      </main>
    );
  }

  return (
    <PanelSessionContext.Provider value={{ required, logout }}>
      <ControlPlaneEventBridge />
      {children}
    </PanelSessionContext.Provider>
  );
}
