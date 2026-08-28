import { useEffect, useState } from "react";
import { rest, type ModelRow, type ProviderRow } from "../api/rest";
import { IconClose } from "./icons";

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
  const [defaultId, setDefaultId] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [testMsg, setTestMsg] = useState("");
  const [form, setForm] = useState({ provider_id: "", display_name: "", base_url: "", api_key: "" });
  const [modelForm, setModelForm] = useState({ providerId: 0, model_id: "", display_name: "", context_window: 128000 });

  const reload = async () => {
    try {
      const [p, m, d] = await Promise.all([rest.providers(), rest.models(), rest.getDefaultModel()]);
      setProviders(p);
      setModels(m);
      setDefaultId(d.default_model_id);
      if (p[0] && !modelForm.providerId) setModelForm((f) => ({ ...f, providerId: p[0].id }));
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const [p, m, d] = await Promise.all([rest.providers(), rest.models(), rest.getDefaultModel()]);
        if (cancelled) return;
        setProviders(p);
        setModels(m);
        setDefaultId(d.default_model_id);
        if (p[0]) setModelForm((f) => (f.providerId ? f : { ...f, providerId: p[0].id }));
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-[var(--color-border)] bg-surface p-5 shadow-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold">模型与供应商</h2>
            <p className="mt-0.5 text-xs text-faint">OpenAI 兼容接口，密钥加密存库</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-white">
            <IconClose />
          </button>
        </div>
        {err && <p className="mb-3 rounded-lg bg-red-950/40 px-3 py-2 text-xs text-red-300">{err}</p>}
        {testMsg && <p className="mb-3 rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted">{testMsg}</p>}

        <section className="mb-5">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">新建供应商</h3>
          <div className="grid grid-cols-2 gap-2">
            <input placeholder="slug（如 openai）" className="ui-input text-xs" value={form.provider_id} onChange={(e) => setForm({ ...form, provider_id: e.target.value })} />
            <input placeholder="显示名" className="ui-input text-xs" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            <input placeholder="base_url" className="ui-input col-span-2 text-xs" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            <input placeholder="api_key" type="password" className="ui-input col-span-2 text-xs" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
          </div>
          <button
            className="ui-btn-primary mt-2 text-xs"
            onClick={async () => {
              setErr("");
              try {
                await rest.createProvider(form);
                setForm({ provider_id: "", display_name: "", base_url: "", api_key: "" });
                await reload();
                onChanged();
              } catch (e) {
                setErr(String(e));
              }
            }}
          >
            保存供应商
          </button>
        </section>

        <section className="mb-5">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">供应商</h3>
          <div className="space-y-1.5">
            {providers.map((p) => (
              <div key={p.id} className="flex items-center justify-between rounded-xl border border-[var(--color-border)] bg-surface-2 px-3 py-2 text-xs">
                <span>
                  {p.display_name} <span className="text-faint">({p.provider_id})</span>
                </span>
                <div className="flex gap-3">
                  <button
                    className="text-accent hover:underline"
                    onClick={async () => {
                      const r = await rest.testProvider(p.id);
                      setTestMsg(r.ok ? `hello ok: ${r.reply || ""}` : `hello 失败: ${r.error || ""}（仍可保存）`);
                    }}
                  >
                    hello 探测
                  </button>
                  <button
                    className="text-red-400 hover:underline"
                    onClick={async () => {
                      await rest.deleteProvider(p.id);
                      await reload();
                      onChanged();
                    }}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
          {providers.length === 0 && <p className="text-xs text-faint">暂无供应商。未配置时走 heuristic 演示。</p>}
        </section>

        <section className="mb-5">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">新建模型</h3>
          <div className="grid grid-cols-2 gap-2">
            <select className="ui-input text-xs" value={modelForm.providerId} onChange={(e) => setModelForm({ ...modelForm, providerId: Number(e.target.value) })}>
              <option value={0}>选择供应商</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
            <input placeholder="model_id" className="ui-input text-xs" value={modelForm.model_id} onChange={(e) => setModelForm({ ...modelForm, model_id: e.target.value })} />
            <input placeholder="显示名" className="ui-input text-xs" value={modelForm.display_name} onChange={(e) => setModelForm({ ...modelForm, display_name: e.target.value })} />
            <input placeholder="context_window" type="number" className="ui-input text-xs" value={modelForm.context_window} onChange={(e) => setModelForm({ ...modelForm, context_window: Number(e.target.value) })} />
          </div>
          <button
            className="ui-btn-primary mt-2 text-xs"
            onClick={async () => {
              if (!modelForm.providerId) return setErr("请选择供应商");
              try {
                await rest.createModel(modelForm.providerId, {
                  model_id: modelForm.model_id,
                  display_name: modelForm.display_name || modelForm.model_id,
                  context_window: modelForm.context_window,
                });
                await reload();
                onChanged();
              } catch (e) {
                setErr(String(e));
              }
            }}
          >
            保存模型
          </button>
        </section>

        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
            模型 · 当前兜底 {defaultId ?? "未设"}
          </h3>
          <div className="space-y-1.5">
            {models.map((m) => (
              <div key={m.id} className="flex items-center justify-between rounded-xl border border-[var(--color-border)] bg-surface-2 px-3 py-2 text-xs">
                <span>
                  {m.display_name}{" "}
                  <span className="text-faint">
                    {m.provider_name} / {m.model_id}
                  </span>
                </span>
                <div className="flex gap-3">
                  <button
                    className="text-accent hover:underline"
                    onClick={async () => {
                      await rest.putDefaultModel(m.id);
                      await reload();
                      onChanged();
                    }}
                  >
                    设为兜底
                  </button>
                  <button
                    className="text-red-400 hover:underline"
                    onClick={async () => {
                      await rest.deleteModel(m.id);
                      await reload();
                      onChanged();
                    }}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
