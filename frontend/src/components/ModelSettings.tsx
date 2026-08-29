import { useEffect, useMemo, useState } from "react";
import { rest, type ModelRow, type ProviderRow } from "../api/rest";
import { IconCheck, IconClose, IconPlus, IconSettings, IconTrash } from "./icons";

type DraftModel = {
  key: string;
  id?: number;
  model_id: string;
  display_name: string;
  request_thinking: boolean;
  reasoning_effort: string;
  testStatus: "idle" | "testing" | "ok" | "fail";
  testMessage: string;
};

type Draft = {
  pk: number | null;
  provider_id: string;
  display_name: string;
  base_url: string;
  api_key: string;
  api_key_dirty: boolean;
  api_key_from_env: boolean;
  api_key_env: string;
  api_key_set: boolean;
  models: DraftModel[];
  removedIds: number[];
};

const emptyDraft = (): Draft => ({
  pk: null,
  provider_id: "",
  display_name: "",
  base_url: "",
  api_key: "",
  api_key_dirty: false,
  api_key_from_env: false,
  api_key_env: "",
  api_key_set: false,
  models: [],
  removedIds: [],
});

function fromSaved(p: ProviderRow, models: ModelRow[]): Draft {
  return {
    pk: p.id,
    provider_id: p.provider_id,
    display_name: p.display_name,
    base_url: p.base_url,
    api_key: "",
    api_key_dirty: false,
    api_key_from_env: p.api_key_from_env,
    api_key_env: p.api_key_env || "",
    api_key_set: p.api_key_set,
    models: models
      .filter((m) => m.provider_id === p.id)
      .map((m) => ({
        key: `m-${m.id}`,
        id: m.id,
        model_id: m.model_id,
        display_name: m.display_name,
        request_thinking: Boolean(m.request_thinking),
        reasoning_effort: m.reasoning_effort || "",
        testStatus: "idle",
        testMessage: "",
      })),
    removedIds: [],
  };
}

function snapshot(d: Draft) {
  return JSON.stringify({
    pk: d.pk,
    provider_id: d.provider_id,
    display_name: d.display_name,
    base_url: d.base_url,
    api_key_dirty: d.api_key_dirty,
    api_key: d.api_key_dirty ? d.api_key : "",
    api_key_from_env: d.api_key_from_env,
    api_key_env: d.api_key_env,
    models: d.models.map((m) => ({
      id: m.id,
      model_id: m.model_id,
      display_name: m.display_name,
      request_thinking: m.request_thinking,
      reasoning_effort: m.reasoning_effort,
    })),
    removedIds: d.removedIds,
  });
}

export default function ModelSettings({
  open,
  onClose,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [models, setModels] = useState<ModelRow[]>([]);
  const [selected, setSelected] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [baseline, setBaseline] = useState("");
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const dirty = snapshot(draft) !== baseline;
  const modelCount = useMemo(() => {
    const n: Record<number, number> = {};
    for (const m of models) n[m.provider_id] = (n[m.provider_id] || 0) + 1;
    return n;
  }, [models]);

  const reload = async (keepPk?: number | "new" | null) => {
    const [p, m] = await Promise.all([rest.providers(), rest.models()]);
    setProviders(p);
    setModels(m);
    const want = keepPk === undefined ? selected : keepPk;
    if (want === "new") {
      const d = emptyDraft();
      setSelected("new");
      setDraft(d);
      setBaseline(snapshot(d));
      return;
    }
    const pk = typeof want === "number" ? want : p[0]?.id ?? null;
    const row = p.find((x) => x.id === pk) ?? p[0];
    if (!row) {
      const d = emptyDraft();
      setSelected(null);
      setDraft(d);
      setBaseline(snapshot(d));
      return;
    }
    const d = fromSaved(row, m);
    setSelected(row.id);
    setDraft(d);
    setBaseline(snapshot(d));
  };

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const [p, m] = await Promise.all([rest.providers(), rest.models()]);
        if (cancelled) return;
        setProviders(p);
        setModels(m);
        if (p[0]) {
          const d = fromSaved(p[0], m);
          setSelected(p[0].id);
          setDraft(d);
          setBaseline(snapshot(d));
        } else {
          const d = emptyDraft();
          setSelected("new");
          setDraft(d);
          setBaseline(snapshot(d));
        }
        setErr("");
        setSaved(false);
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const showSaved = saved && !dirty && !saving;

  useEffect(() => {
    if (!showSaved) return;
    const t = window.setTimeout(() => setSaved(false), 2500);
    return () => window.clearTimeout(t);
  }, [showSaved]);

  if (!open) return null;

  const switchTo = async (next: number | "new") => {
    if (next === selected) return;
    if (dirty && !window.confirm("有未保存的更改，切换将丢弃。继续？")) return;
    setErr("");
    setSaved(false);
    if (next === "new") {
      const d = emptyDraft();
      setSelected("new");
      setDraft(d);
      setBaseline(snapshot(d));
      return;
    }
    const row = providers.find((x) => x.id === next);
    if (!row) return;
    const d = fromSaved(row, models);
    setSelected(next);
    setDraft(d);
    setBaseline(snapshot(d));
  };

  const save = async () => {
    setErr("");
    setSaved(false);
    if (!dirty && draft.pk != null) {
      setSaved(true);
      return;
    }
    const slug = draft.provider_id.trim();
    const name = draft.display_name.trim();
    const url = draft.base_url.trim();
    if (!name) return setErr("请填写显示名称");
    if (!url) return setErr("请填写基础 URL");
    if (draft.pk == null && !slug) return setErr("请填写供应商 ID");
    if (draft.api_key_from_env && !draft.api_key_env.trim()) return setErr("请填写环境变量名称");
    const seen = new Set<string>();
    for (const row of draft.models) {
      const mid = row.model_id.trim();
      if (!mid) return setErr("每条模型都需要填写模型 ID");
      if (seen.has(mid)) return setErr(`重复的模型 ID：${mid}`);
      seen.add(mid);
    }
    setSaving(true);
    try {
      let pk = draft.pk;
      if (pk == null) {
        const created = await rest.createProvider({
          provider_id: slug,
          display_name: name,
          base_url: url,
          api_key: draft.api_key_from_env ? "" : draft.api_key,
          api_key_from_env: draft.api_key_from_env,
          api_key_env: draft.api_key_from_env ? draft.api_key_env.trim() : null,
        });
        pk = created.id;
      } else {
        const body: Parameters<typeof rest.patchProvider>[1] = {
          display_name: name,
          base_url: url,
          api_key_from_env: draft.api_key_from_env,
          api_key_env: draft.api_key_from_env ? draft.api_key_env.trim() : null,
        };
        if (!draft.api_key_from_env && draft.api_key_dirty) body.api_key = draft.api_key;
        await rest.patchProvider(pk, body);
      }
      for (const id of draft.removedIds) {
        await rest.deleteModel(id);
      }
      for (const row of draft.models) {
        const payload = {
          model_id: row.model_id.trim(),
          display_name: row.display_name.trim() || row.model_id.trim(),
          request_thinking: row.request_thinking,
          reasoning_effort: row.request_thinking ? row.reasoning_effort.trim() || null : null,
        };
        if (row.id) {
          await rest.patchModel(row.id, payload);
        } else {
          await rest.createModel(pk, payload);
        }
      }
      onChanged();
      await reload(pk);
      setSaved(true);
    } catch (e) {
      setErr(String(e));
      setSaved(false);
    } finally {
      setSaving(false);
    }
  };

  const removeProvider = async () => {
    if (draft.pk == null) {
      if (providers[0]) await switchTo(providers[0].id);
      else {
        const d = emptyDraft();
        setDraft(d);
        setBaseline(snapshot(d));
      }
      return;
    }
    if (!window.confirm(`删除供应商「${draft.display_name || draft.provider_id}」及其全部模型？`)) return;
    try {
      await rest.deleteProvider(draft.pk);
      onChanged();
      const restProviders = providers.filter((p) => p.id !== draft.pk);
      await reload(restProviders[0]?.id ?? "new");
    } catch (e) {
      setErr(String(e));
    }
  };

  const testRow = async (key: string) => {
    const row = draft.models.find((m) => m.key === key);
    if (!row || !row.model_id.trim()) return;
    setDraft((d) => ({
      ...d,
      models: d.models.map((m) => (m.key === key ? { ...m, testStatus: "testing", testMessage: "" } : m)),
    }));
    try {
      const r = await rest.probeLlm({
        base_url: draft.base_url.trim(),
        model_id: row.model_id.trim(),
        api_key: draft.api_key_from_env ? undefined : draft.api_key_dirty ? draft.api_key : undefined,
        api_key_from_env: draft.api_key_from_env,
        api_key_env: draft.api_key_from_env ? draft.api_key_env.trim() : undefined,
        provider_id: draft.pk,
        provider_slug: draft.provider_id.trim(),
      });
      const ok = r.ok;
      const detail = String((ok ? r.reply : r.error) || "").trim().slice(0, 240);
      const msg = ok ? (detail ? `连接成功 · ${detail}` : "连接成功") : detail ? `连接失败 · ${detail}` : "连接失败";
      setDraft((d) => ({
        ...d,
        models: d.models.map((m) => (m.key === key ? { ...m, testStatus: ok ? "ok" : "fail", testMessage: msg } : m)),
      }));
    } catch (e) {
      setDraft((d) => ({
        ...d,
        models: d.models.map((m) => (m.key === key ? { ...m, testStatus: "fail", testMessage: String(e) } : m)),
      }));
    }
  };

  const isNew = selected === "new" || draft.pk == null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 backdrop-blur-sm sm:p-6" onClick={onClose}>
      <div
        className="flex h-[min(92vh,760px)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-[var(--color-border)] bg-surface shadow-panel md:flex-row"
        onClick={(e) => e.stopPropagation()}
      >
        <aside className="flex max-h-44 w-full shrink-0 flex-col border-b border-[var(--color-border)] bg-bg/60 md:max-h-none md:w-64 md:border-b-0 md:border-r">
          <div className="flex items-center gap-2 px-4 py-3 md:py-4">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-accent-fg">
              <IconSettings className="h-4 w-4" />
            </span>
            <h2 className="text-base font-semibold">模型配置</h2>
          </div>
          <div className="px-3">
            <button
              className="flex w-full items-center justify-center gap-1 rounded-xl border border-dashed border-[var(--color-border-strong)] px-3 py-2.5 text-sm text-muted hover:border-accent hover:text-white"
              onClick={() => switchTo("new")}
            >
              <IconPlus className="h-4 w-4" />
              添加供应商
            </button>
          </div>
          <nav className="mt-2 flex min-h-0 flex-1 gap-1 overflow-x-auto overflow-y-hidden px-2 pb-3 md:mt-3 md:flex-col md:space-y-0.5 md:overflow-y-auto md:overflow-x-hidden md:pb-4">
            {providers.map((p) => {
              const active = selected === p.id;
              const n = modelCount[p.id] || 0;
              return (
                <button
                  key={p.id}
                  onClick={() => switchTo(p.id)}
                  className={`w-44 shrink-0 rounded-xl px-3 py-2.5 text-left transition md:w-full ${
                    active ? "bg-accent-dim ring-1 ring-accent/40" : "hover:bg-surface-2"
                  }`}
                >
                  <div className={`truncate text-sm font-medium ${active ? "text-accent" : ""}`}>{p.display_name}</div>
                  <div className="truncate text-[11px] text-faint">
                    {p.provider_id} · {n} 个模型
                  </div>
                </button>
              );
            })}
            {isNew && providers.length > 0 && (
              <div className="w-44 shrink-0 rounded-xl bg-accent-dim px-3 py-2.5 ring-1 ring-accent/40 md:w-full">
                <div className="text-sm font-medium text-accent">新供应商</div>
                <div className="text-[11px] text-faint">尚未保存</div>
              </div>
            )}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-start justify-between gap-3 px-5 py-4 sm:px-7">
            <div>
              <h3 className="text-lg font-semibold">{isNew ? "添加供应商" : "编辑供应商"}</h3>
              <p className="mt-1 text-xs text-faint">配置与 OpenAI 兼容的供应商（chat/completions）。</p>
            </div>
            <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-white" aria-label="关闭">
              <IconClose />
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4 sm:px-7">
            {err && <p className="mb-3 rounded-lg bg-red-950/40 px-3 py-2 text-xs text-red-300">{err}</p>}
            {showSaved && !err && (
              <p role="status" className="mb-3 rounded-lg bg-accent-dim px-3 py-2 text-xs text-accent">
                已保存
              </p>
            )}

            <label className="mb-1 block text-sm font-medium">供应商 ID</label>
            <input
              className="ui-input mb-1"
              value={draft.provider_id}
              disabled={!isNew}
              placeholder="填写 OpenAI 兼容的供应商 ID"
              onChange={(e) => setDraft({ ...draft, provider_id: e.target.value.toLowerCase() })}
            />
            <p className="mb-4 text-[11px] text-faint">使用小写字母、数字、连字符或下划线，创建后不可修改</p>

            <label className="mb-1 block text-sm font-medium">显示名称</label>
            <input
              className="ui-input mb-4"
              value={draft.display_name}
              placeholder="填写 OpenAI 兼容的供应商名称"
              onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
            />

            <label className="mb-1 block text-sm font-medium">基础 URL</label>
            <input
              className="ui-input mb-4"
              value={draft.base_url}
              placeholder="填写 OpenAI 兼容的接口地址"
              onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
            />

            <label className="mb-3 flex cursor-pointer items-start gap-2 text-sm text-muted">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-[var(--color-border-strong)] bg-surface-2 accent-[var(--color-accent)]"
                checked={draft.api_key_from_env}
                onChange={(e) => setDraft({ ...draft, api_key_from_env: e.target.checked })}
              />
              <span>从环境变量读取 API 密钥</span>
            </label>

            {draft.api_key_from_env ? (
              <>
                <label className="mb-1 block text-sm font-medium">环境变量名称</label>
                <input
                  className="ui-input mb-1 font-mono"
                  value={draft.api_key_env}
                  placeholder="OPENAI_API_KEY"
                  onChange={(e) => setDraft({ ...draft, api_key_env: e.target.value })}
                />
                <p className="mb-5 text-[11px] text-faint">只保存变量名。密钥从进程环境或项目 .env 读取，不写入数据库。</p>
              </>
            ) : (
              <>
                <label className="mb-1 block text-sm font-medium">API 密钥</label>
                <input
                  className="ui-input mb-1"
                  type="password"
                  value={draft.api_key}
                  placeholder={draft.api_key_set ? "已保存。填写则替换，留空不改" : "可选。本地无鉴权端点可留空"}
                  onChange={(e) => setDraft({ ...draft, api_key: e.target.value, api_key_dirty: true })}
                />
                <p className="mb-5 text-[11px] text-faint">可选。需要走环境变量时勾选上方选项，改为填写变量名。</p>
              </>
            )}

            <div className="mb-2 text-sm font-medium">模型</div>
            <div className="space-y-2">
              {draft.models.map((row) => (
                <div key={row.key}>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <input
                      className="ui-input font-mono text-xs"
                      placeholder="填写 OpenAI 兼容的模型 ID"
                      value={row.model_id}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          models: draft.models.map((m) => (m.key === row.key ? { ...m, model_id: e.target.value } : m)),
                        })
                      }
                    />
                    <input
                      className="ui-input text-xs"
                      placeholder="填写 OpenAI 兼容的模型名称"
                      value={row.display_name}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          models: draft.models.map((m) =>
                            m.key === row.key ? { ...m, display_name: e.target.value } : m,
                          ),
                        })
                      }
                    />
                    <label className="flex shrink-0 items-center gap-1 text-[11px] text-muted">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 rounded border-[var(--color-border-strong)] bg-surface-2 accent-[var(--color-accent)]"
                        checked={row.request_thinking}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            models: draft.models.map((m) =>
                              m.key === row.key ? { ...m, request_thinking: e.target.checked } : m,
                            ),
                          })
                        }
                      />
                      请求思考
                    </label>
                    {row.request_thinking && (
                      <input
                        className="ui-input w-24 text-xs"
                        placeholder="effort"
                        value={row.reasoning_effort}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            models: draft.models.map((m) =>
                              m.key === row.key ? { ...m, reasoning_effort: e.target.value } : m,
                            ),
                          })
                        }
                      />
                    )}
                    <div className="flex shrink-0 gap-2">
                    <button
                      className="ui-btn-ghost flex-1 px-3 text-xs sm:flex-none"
                      disabled={!row.model_id.trim() || !draft.base_url.trim() || row.testStatus === "testing"}
                      onClick={() => testRow(row.key)}
                    >
                      {row.testStatus === "testing" ? "测试中" : "测试"}
                    </button>
                    <button
                      className="ui-btn-ghost px-2 text-red-400 hover:text-red-300"
                      aria-label="删除模型"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          models: draft.models.filter((m) => m.key !== row.key),
                          removedIds: row.id ? [...draft.removedIds, row.id] : draft.removedIds,
                        })
                      }
                    >
                      <IconTrash />
                    </button>
                    </div>
                  </div>
                  {row.testMessage && (
                    <p className={`mt-1 text-[11px] ${row.testStatus === "ok" ? "text-accent" : "text-red-300"}`}>
                      {row.testMessage}
                    </p>
                  )}
                </div>
              ))}
            </div>
            <button
              className="mt-3 text-sm text-accent hover:underline"
              onClick={() =>
                setDraft({
                  ...draft,
                  models: [
                    ...draft.models,
                    {
                      key: `new-${Date.now()}`,
                      model_id: "",
                      display_name: "",
                      request_thinking: false,
                      reasoning_effort: "",
                      testStatus: "idle",
                      testMessage: "",
                    },
                  ],
                })
              }
            >
              + 添加模型
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-border)] px-5 py-3 sm:px-7">
            <button
              className="ui-btn-primary min-w-[5.5rem]"
              disabled={saving}
              onClick={save}
              aria-live="polite"
            >
              {saving ? (
                "保存中…"
              ) : showSaved ? (
                <>
                  <IconCheck className="h-4 w-4" />
                  已保存
                </>
              ) : (
                "保存"
              )}
            </button>
            <button
              className="ui-btn-ghost border-red-500/30 text-red-300 hover:bg-red-950/40 hover:text-red-200"
              onClick={removeProvider}
              disabled={saving}
            >
              删除供应商
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
