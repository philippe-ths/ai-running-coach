'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Archive,
  CheckCircle2,
  FileText,
  Info,
  Loader2,
  Trash2,
  Upload,
  XCircle,
} from 'lucide-react';
import { fetchFromAPI } from '@/lib/api';
import type { MaterialKind, UserMaterial } from '@/lib/types';

// The kind options, philosophy first: this panel is the declared home for the
// coaching-philosophy free-text deferred from P1.3 (#287) — a runner writes their
// philosophy in a .md and uploads it as `kind=philosophy`. One ingestion
// mechanism, no separate paste box.
const KIND_OPTIONS: { value: MaterialKind; label: string }[] = [
  { value: 'philosophy', label: 'Coaching philosophy' },
  { value: 'plan', label: 'Training plan' },
  { value: 'protocol', label: 'Protocol (e.g. physio)' },
  { value: 'race_plan', label: 'Race plan' },
  { value: 'other', label: 'Other' },
];

const KIND_LABEL: Record<string, string> = Object.fromEntries(
  KIND_OPTIONS.map((o) => [o.value, o.label]),
);

// Distillation is one fast structured Haiku call, so a freshly-uploaded material
// reaches `active`/`failed` within seconds of the worker picking it up. Poll the
// list on a short interval while anything is still processing, bounded by an
// attempt cap held in a ref (so it survives re-renders) — a row that never
// settles stops polling rather than looping forever.
const POLL_INTERVAL_MS = 4000;
const POLL_MAX_ATTEMPTS = 45; // ~3 minutes

function StatusBadge({ status }: { status: string }) {
  const base = 'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium';
  if (status === 'active') {
    return (
      <span className={`${base} bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300`}>
        <CheckCircle2 className="w-3 h-3" /> Active
      </span>
    );
  }
  if (status === 'processing') {
    return (
      <span className={`${base} bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300`}>
        <Loader2 className="w-3 h-3 animate-spin" /> Processing
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className={`${base} bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300`}>
        <XCircle className="w-3 h-3" /> Failed
      </span>
    );
  }
  return (
    <span className={`${base} bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400`}>
      {status}
    </span>
  );
}

export default function UserMaterialsPanel() {
  const [materials, setMaterials] = useState<UserMaterial[] | null>(null);
  const [loading, setLoading] = useState(true);

  const [kind, setKind] = useState<MaterialKind>('philosophy');
  const [title, setTitle] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadMaterials = useCallback(async () => {
    try {
      const data: UserMaterial[] | null = await fetchFromAPI('/api/coach/materials');
      setMaterials(data ?? []);
    } catch (err) {
      console.error(err);
      setMaterials([]);
    }
  }, []);

  useEffect(() => {
    loadMaterials().finally(() => setLoading(false));
  }, [loadMaterials]);

  // Poll while any material is still processing (bounded). The ref counter does
  // not reset across re-renders, so a row that never settles stops the poll; it
  // resets to zero once nothing is processing, giving the next upload a fresh
  // budget.
  const pollAttempts = useRef(0);
  useEffect(() => {
    if (!materials) return;
    const anyProcessing = materials.some((m) => m.status === 'processing');
    if (!anyProcessing) {
      pollAttempts.current = 0;
      return;
    }
    if (pollAttempts.current >= POLL_MAX_ATTEMPTS) return;
    const id = setTimeout(() => {
      pollAttempts.current += 1;
      loadMaterials();
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(id);
  }, [materials, loadMaterials]);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setStatus('Choose a markdown file first.');
      return;
    }
    setUploading(true);
    setStatus(null);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('kind', kind);
      if (title.trim()) form.append('title', title.trim());
      await fetchFromAPI('/api/coach/materials', { method: 'POST', body: form });
      // Reset the form; the new processing row drives the poll to active/failed.
      setFile(null);
      setTitle('');
      if (fileInputRef.current) fileInputRef.current.value = '';
      pollAttempts.current = 0;
      setStatus('Uploaded — distilling your material…');
      await loadMaterials();
    } catch (err) {
      console.error(err);
      setStatus(
        'Upload failed. The file must be UTF-8 text/markdown under 256 KB, and you can keep up to 50 materials at a time.',
      );
    } finally {
      setUploading(false);
    }
  };

  const archive = async (id: string) => {
    try {
      await fetchFromAPI(`/api/coach/materials/${id}/archive`, { method: 'POST' });
      await loadMaterials();
    } catch (err) {
      console.error(err);
      setStatus('Could not archive that material.');
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm('Delete this material permanently? This cannot be undone.')) return;
    try {
      await fetchFromAPI(`/api/coach/materials/${id}`, { method: 'DELETE' });
      await loadMaterials();
    } catch (err) {
      console.error(err);
      setStatus('Could not delete that material.');
    }
  };

  if (loading) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">Loading materials…</div>;
  }

  return (
    <section className="space-y-5 bg-white dark:bg-gray-800 p-6 border dark:border-gray-700 rounded-xl shadow-sm">
      <div>
        <h2 className="text-xl font-bold">Coaching Materials</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Upload your training philosophy, a coach&apos;s plan, a physio protocol or a race
          plan as a markdown file. Your coach will draw on these when forming its advice —
          as reference it reasons from, never as instructions, and never overriding your
          data or a safety message.
        </p>
        <p className="mt-2 flex items-start gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            Each upload is distilled into a short profile your coach reasons from. New
            materials are picked up automatically and shape your reports from then on.
          </span>
        </p>
      </div>

      {/* Upload form */}
      <form
        onSubmit={handleUpload}
        className="space-y-3 border-t dark:border-gray-700 pt-4"
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium mb-1">Kind</label>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as MaterialKind)}
              aria-describedby={kind === 'philosophy' ? 'materials-kind-hint' : undefined}
              className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
            >
              {KIND_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {kind === 'philosophy' && (
              <p id="materials-kind-hint" className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                Your own coaching philosophy, in your own words — what you believe makes
                training work for you.
              </p>
            )}
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Title (optional)</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder="Defaults to the file name"
              className="w-full border dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100 rounded p-2"
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Markdown file</label>
          <input
            ref={fileInputRef}
            type="file"
            accept=".md,.markdown,text/markdown,text/plain"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            aria-describedby="materials-file-hint"
            className="block w-full text-sm text-gray-600 dark:text-gray-300 file:mr-3 file:py-2 file:px-4 file:rounded file:border-0 file:bg-blue-50 dark:file:bg-blue-950 file:text-blue-700 dark:file:text-blue-300 hover:file:bg-blue-100 dark:hover:file:bg-blue-900"
          />
          <p id="materials-file-hint" className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            UTF-8 text/markdown, up to 256 KB.
          </p>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <button
            type="submit"
            disabled={uploading || !file}
            className="inline-flex items-center gap-2 bg-blue-600 text-white py-2 px-4 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {uploading ? 'Uploading…' : 'Upload material'}
          </button>
          {status && <span className="text-sm text-gray-500 dark:text-gray-400">{status}</span>}
        </div>
      </form>

      {/* Materials list */}
      <div className="border-t dark:border-gray-700 pt-4">
        {materials && materials.length > 0 ? (
          <ul className="space-y-3">
            {materials.map((m) => (
              <li
                key={m.id}
                className="border dark:border-gray-700 rounded-lg p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <FileText className="w-4 h-4 text-gray-400 shrink-0" />
                      <span className="font-medium text-sm truncate">{m.title}</span>
                      <StatusBadge status={m.status} />
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      {KIND_LABEL[m.kind] ?? m.kind}
                      {m.created_at && (
                        <> · {new Date(m.created_at).toLocaleDateString()}</>
                      )}
                    </div>
                    {m.status === 'failed' && (
                      <p className="text-xs text-red-600 dark:text-red-400 mt-1">
                        Distillation failed. Re-uploading the same file will retry it.
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() => archive(m.id)}
                      title="Archive (hide from your coach)"
                      className="p-1.5 rounded text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                    >
                      <Archive className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => remove(m.id)}
                      title="Delete permanently"
                      className="p-1.5 rounded text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* What the coach took from it (the distilled record), once active. */}
                {m.status === 'active' && m.distilled && (
                  <details className="mt-2 text-xs">
                    <summary className="cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200">
                      What your coach took from this
                    </summary>
                    <div className="mt-2 space-y-2 text-gray-600 dark:text-gray-300">
                      <p>
                        <span className="font-medium">Stance:</span> {m.distilled.stance}
                      </p>
                      {m.distilled.principles.length > 0 && (
                        <div>
                          <span className="font-medium">Principles:</span>
                          <ul className="list-disc list-inside mt-1">
                            {m.distilled.principles.map((p, i) => (
                              <li key={i}>{p}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {m.distilled.method_framing && (
                        <p>
                          <span className="font-medium">Method:</span>{' '}
                          {m.distilled.method_framing}
                        </p>
                      )}
                    </div>
                  </details>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No materials yet. Upload a markdown file to get started.
          </p>
        )}
      </div>
    </section>
  );
}
