import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { UploadResult } from "../api/types";
import { PageHeader } from "../components/ui";
import { ACCEPTED_EXT, extOk, formatBytes } from "../utils/format";

const MAX_BYTES = 100 * 1024 * 1024;
interface Item { id: number; file: File; state: "queued" | "uploading" | "done" | "error"; message?: string; result?: UploadResult }

export default function Upload() {
  const [items, setItems] = useState<Item[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const busy = useRef(false);
  const nextId = useRef(0);

  const patch = (id: number, p: Partial<Item>) => setItems((cur) => cur.map((i) => (i.id === id ? { ...i, ...p } : i)));

  async function run(batch: Item[]) {
    if (busy.current) return;
    busy.current = true;
    for (const it of batch) {
      if (it.state === "error") continue;
      patch(it.id, { state: "uploading" });
      try {
        const fd = new FormData();
        fd.append("file", it.file);
        const result = await api<UploadResult>("/documents", { method: "POST", body: fd });
        patch(it.id, { state: "done", result });
      } catch (e) { patch(it.id, { state: "error", message: e instanceof Error ? e.message : "Upload failed" }); }
    }
    busy.current = false;
    void qc.invalidateQueries({ queryKey: ["documents"] });
    void qc.invalidateQueries({ queryKey: ["stats"] });
  }

  function add(files: FileList | File[]) {
    const batch: Item[] = Array.from(files).map((file) => {
      const id = nextId.current++;
      if (!extOk(file.name)) return { id, file, state: "error", message: `Unsupported type. Accepted: ${ACCEPTED_EXT.join(" ")}` };
      if (file.size > MAX_BYTES) return { id, file, state: "error", message: `Larger than ${formatBytes(MAX_BYTES)}` };
      if (file.size === 0) return { id, file, state: "error", message: "File is empty" };
      return { id, file, state: "queued" };
    });
    setItems((cur) => [...batch, ...cur]);
    void run(batch);
  }
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) add(e.dataTransfer.files); };

  return (
    <>
      <PageHeader title="Upload documents" subtitle="PDF (digital or scanned), JPG, PNG, TIFF, XLSX, XLS and CSV up to 100 MB" />
      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-12 text-center transition ${drag ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-white"}`}>
        <p className="text-sm text-slate-600">Drag files here, or</p>
        <button className="btn-primary mt-3" onClick={() => input.current?.click()}>Choose files</button>
        <input ref={input} type="file" multiple hidden aria-label="Choose files" accept={ACCEPTED_EXT.join(",")} onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
      </div>
      {items.length > 0 && (
        <ul className="card mt-6 divide-y divide-slate-100" aria-live="polite">
          {items.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
              <div className="min-w-0"><div className="truncate font-medium">{i.file.name}</div><div className="text-xs text-slate-500">{formatBytes(i.file.size)}</div></div>
              <div className="text-right">
                {i.state === "queued" && <span className="text-slate-500">Queued</span>}
                {i.state === "uploading" && <span className="text-brand-600">Uploading…</span>}
                {i.state === "error" && <span className="text-red-700">{i.message}</span>}
                {i.state === "done" && i.result && (
                  <span className="text-emerald-700">
                    Uploaded · <Link className="underline" to={`/documents/${i.result.document.id}`}>view</Link>
                    {i.result.duplicate_of.length > 0 && <span className="ml-2 text-amber-700">identical content already uploaded ({i.result.duplicate_of.length}); kept, not merged</span>}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
