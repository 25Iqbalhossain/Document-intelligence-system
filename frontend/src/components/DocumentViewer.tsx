import { useEffect, useState } from "react";
import { fileBlobUrl } from "../api/client";

/** Shows the original file through an authenticated blob URL. `page` jumps a PDF to a source page. */
export function DocumentViewer({ documentId, mime, page }: { documentId: string; mime: string; page?: number | null }) {
  const [src, setSrc] = useState<{ url: string; type: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    setSrc(null); setError(null);
    fileBlobUrl(documentId).then((r) => { if (cancelled) URL.revokeObjectURL(r.url); else { revoked = r.url; setSrc(r); } })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => { cancelled = true; if (revoked) URL.revokeObjectURL(revoked); };
  }, [documentId]);

  if (error) return <p className="p-4 text-sm text-red-700">Could not load the file: {error}</p>;
  if (!src) return <p className="p-4 text-sm text-slate-500">Loading file…</p>;
  const type = src.type || mime;
  if (type === "application/pdf") return <iframe title="Original document" className="h-full min-h-[600px] w-full rounded" src={`${src.url}#page=${page ?? 1}&toolbar=1`} key={`${src.url}-${page ?? 1}`} />;
  if (type.startsWith("image/")) return <img alt="Original document" src={src.url} className="max-h-full w-full rounded object-contain" />;
  return <div className="p-4 text-sm text-slate-600">Preview is not available for this file type. Review the extracted values and source cells beside it.</div>;
}
