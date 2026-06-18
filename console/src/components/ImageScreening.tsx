import { useCallback, useEffect, useRef, useState } from "react";
import {
  ImageUp, Upload, Loader2, Trash2, X, FileWarning, AlertTriangle, Info,
} from "lucide-react";
import type { ScreenResult } from "../lib/types";
import { screenImages, ApiError } from "../lib/api";
import { SCREEN_RISK_UI, SCREEN_FLAG_UI } from "./screeningUi";
import {
  SCREEN_TITLE, SCREEN_SUBTITLE, SCREEN_DROPZONE, SCREEN_DROPZONE_HINT,
  SCREEN_PICK_FILES, SCREEN_ANALYZE, SCREEN_ANALYZING, SCREEN_ANALYZING_HINT,
  SCREEN_CLEAR_ALL, SCREEN_REMOVE_FILE, SCREEN_SELECTED_COUNT, SCREEN_NO_FILES,
  SCREEN_RESULTS_TITLE, SCREEN_WAGON_TYPE, SCREEN_MAIN_CARGO, SCREEN_RISK_LEVEL,
  SCREEN_FLAGS_TITLE, SCREEN_SUMMARY_LABEL, SCREEN_SECONDS, SCREEN_RESULT_ERROR,
  SCREEN_ERROR, SCREEN_UNSUPPORTED, SCREEN_DISCLAIMER, SCREEN_FLAG_NAME,
  SCREEN_FLAG_VALUE, RISK_BAND_SHORT,
} from "../lib/uz";

const ACCEPT = "image/jpeg,image/png";
const ACCEPT_TYPES = new Set(["image/jpeg", "image/png"]);

interface PendingFile {
  id:      string;
  file:    File;
  preview: string;   // object URL for the thumbnail
}

let _seq = 0;
function nextId(): string {
  _seq += 1;
  return `f${_seq}-${Date.now()}`;
}

export function ImageScreening() {
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [results, setResults] = useState<ScreenResult[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);

  // Revoke every object URL on unmount to avoid leaking blob references.
  useEffect(() => {
    return () => { pending.forEach((p) => URL.revokeObjectURL(p.preview)); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addFiles = useCallback((files: FileList | File[]) => {
    const incoming = Array.from(files);
    const accepted = incoming.filter((f) => ACCEPT_TYPES.has(f.type));
    const rejected = incoming.length - accepted.length;

    if (rejected > 0) setError(SCREEN_UNSUPPORTED);
    else setError(null);

    if (accepted.length === 0) return;

    setPending((prev) => [
      ...prev,
      ...accepted.map((file) => ({
        id:      nextId(),
        file,
        preview: URL.createObjectURL(file),
      })),
    ]);
  }, []);

  const removeFile = useCallback((id: string) => {
    setPending((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target) URL.revokeObjectURL(target.preview);
      return prev.filter((p) => p.id !== id);
    });
  }, []);

  const clearAll = useCallback(() => {
    setPending((prev) => {
      prev.forEach((p) => URL.revokeObjectURL(p.preview));
      return [];
    });
    setResults([]);
    setError(null);
  }, []);

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files);
    e.target.value = "";   // allow re-selecting the same file
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  };

  const handleAnalyze = async () => {
    if (pending.length === 0 || analyzing) return;
    setAnalyzing(true);
    setError(null);
    setResults([]);
    try {
      const res = await screenImages(pending.map((p) => p.file));
      setResults(res.results);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : SCREEN_ERROR);
    } finally {
      setAnalyzing(false);
    }
  };

  // Pair each result with its uploaded thumbnail by filename (best effort).
  const previewFor = (filename: string): string | undefined =>
    pending.find((p) => p.file.name === filename)?.preview;

  return (
    <section
      aria-labelledby="screen-heading"
      className="section-screen rounded-xl border border-white/10 glass section-tint overflow-hidden shadow-elev-3"
    >
      {/* Header — violet "AI vision / upload" identity */}
      <div className="flex items-center gap-2.5 px-3 py-2.5 border-b border-white/10">
        <span className="grid place-items-center w-7 h-7 rounded-lg section-tile shrink-0" aria-hidden="true">
          <ImageUp size={15} />
        </span>
        <div className="min-w-0 section-bar pl-3">
          <p className="text-[10px] font-semibold uppercase section-eyebrow leading-none">Skrining</p>
          <h2 id="screen-heading" className="text-sm font-bold text-content-primary leading-tight mt-0.5">
            {SCREEN_TITLE}
          </h2>
          <p className="text-xs text-content-muted truncate">{SCREEN_SUBTITLE}</p>
        </div>

        {pending.length > 0 && (
          <button
            onClick={clearAll}
            disabled={analyzing}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded text-sm font-medium border border-surface-border text-content-secondary hover:bg-surface-hover disabled:opacity-50 transition-colors"
          >
            <Trash2 size={13} aria-hidden="true" />
            {SCREEN_CLEAR_ALL}
          </button>
        )}
      </div>

      <div className="p-3 flex flex-col gap-3">
        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`rounded-xl border-2 border-dashed p-6 flex flex-col items-center justify-center gap-2 text-center transition-all surface-sunken ${
            dragOver
              ? "border-violet-500 bg-violet-500/10 shadow-glow-violet"
              : "border-surface-border bg-surface/40 hover:border-violet-500/40"
          }`}
        >
          <span className="grid place-items-center w-12 h-12 rounded-xl section-tile mb-1" aria-hidden="true">
            <Upload size={22} />
          </span>
          <p className="text-sm font-semibold text-content-secondary">{SCREEN_DROPZONE}</p>
          <p className="text-xs text-content-muted">{SCREEN_DROPZONE_HINT}</p>
          <button
            onClick={() => inputRef.current?.click()}
            disabled={analyzing}
            className="press mt-1 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold bg-gradient-to-b from-violet-500 to-violet-600 hover:from-violet-400 hover:to-violet-500 disabled:opacity-50 text-white shadow-elev-2 hover:shadow-glow-violet transition-all"
          >
            <ImageUp size={14} aria-hidden="true" />
            {SCREEN_PICK_FILES}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            multiple
            className="sr-only"
            onChange={onInputChange}
            aria-label={SCREEN_PICK_FILES}
          />
        </div>

        {error && (
          <p className="flex items-center gap-1.5 text-sm text-red-300 bg-red-900/30 rounded px-3 py-1.5" role="alert">
            <AlertTriangle size={14} aria-hidden="true" />
            {error}
          </p>
        )}

        {/* Selected thumbnails */}
        <div>
          <h3 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider section-eyebrow mb-1.5">
            <span className="w-1 h-3 rounded-full bg-accent-screen/70" aria-hidden="true" />
            {SCREEN_SELECTED_COUNT}: {pending.length}
          </h3>
          {pending.length === 0 ? (
            <p className="text-sm text-content-muted">{SCREEN_NO_FILES}</p>
          ) : (
            <ul className="flex flex-wrap gap-2 scene">
              {pending.map((p) => (
                <li
                  key={p.id}
                  className="tilt-soft relative w-24 rounded-lg border border-surface-border overflow-hidden bg-black/40 shadow-elev-2 group"
                >
                  <img
                    src={p.preview}
                    alt={p.file.name}
                    className="w-24 h-20 object-cover"
                  />
                  <p className="px-1 py-0.5 text-[10px] text-content-muted truncate" title={p.file.name}>
                    {p.file.name}
                  </p>
                  <button
                    onClick={() => removeFile(p.id)}
                    disabled={analyzing}
                    aria-label={`${SCREEN_REMOVE_FILE}: ${p.file.name}`}
                    className="absolute top-0.5 right-0.5 p-0.5 rounded bg-black/70 text-content-secondary hover:text-red-300 hover:bg-black disabled:opacity-50 transition-colors"
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Analyze action */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleAnalyze}
            disabled={pending.length === 0 || analyzing}
            aria-busy={analyzing}
            className="press flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-gradient-to-b from-violet-500 to-violet-600 hover:from-violet-400 hover:to-violet-500 disabled:opacity-50 text-white shadow-elev-2 hover:shadow-glow-violet transition-all"
          >
            {analyzing
              ? <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              : <ImageUp size={14} aria-hidden="true" />}
            {analyzing ? SCREEN_ANALYZING : SCREEN_ANALYZE}
          </button>
          {analyzing && (
            <span className="text-sm text-content-muted animate-pulse" role="status">
              {SCREEN_ANALYZING_HINT}
            </span>
          )}
        </div>

        {/* Decision-support disclaimer */}
        <p className="flex items-start gap-1.5 text-xs text-content-muted bg-surface/60 border border-surface-border rounded px-3 py-2">
          <Info size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          {SCREEN_DISCLAIMER}
        </p>

        {/* Results */}
        {results.length > 0 && (
          <div>
            <h3 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider section-eyebrow mb-1.5">
              <span className="w-1 h-3 rounded-full bg-accent-screen/70" aria-hidden="true" />
              {SCREEN_RESULTS_TITLE}
            </h3>
            <ul className="flex flex-col gap-3 scene">
              {results.map((r, i) => (
                <ResultCard key={`${r.filename}-${i}`} result={r} preview={previewFor(r.filename)} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// One result card per analysed image.
// ---------------------------------------------------------------------------
function ResultCard({ result, preview }: { result: ScreenResult; preview?: string }) {
  const risk = SCREEN_RISK_UI[result.risk_band];
  const flagEntries = [
    ["narcotics", result.flags.narcotics],
    ["weapon",    result.flags.weapon],
    ["tobacco",   result.flags.tobacco],
    ["other",     result.flags.other],
  ] as const;

  // High-risk results read as the most "raised"/salient card (red halo).
  const isHigh = result.risk_band === "high";

  return (
    <li className={`section-screen card-3d rounded-xl border border-white/10 glass overflow-hidden animate-rise-in shadow-elev-2 ${
      isHigh ? "halo-high" : "section-tint"
    }`}>
      <div className="flex flex-col sm:flex-row">
        {/* Thumbnail */}
        <div className="sm:w-40 shrink-0 bg-black/50 flex items-center justify-center surface-sunken">
          {preview ? (
            <img src={preview} alt={result.filename} className="w-full h-40 sm:h-full object-contain" />
          ) : (
            <div className="w-full h-40 flex items-center justify-center text-content-muted">
              <ImageUp size={28} className="opacity-30" aria-hidden="true" />
            </div>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0 p-3 flex flex-col gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-mono text-content-secondary truncate" title={result.filename}>
              {result.filename}
            </span>
            {/* Risk badge: icon + text + colour (never colour alone) */}
            <span
              className={`ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold border ${risk.cls}`}
            >
              {risk.icon}
              {SCREEN_RISK_LEVEL}: {RISK_BAND_SHORT[result.risk_band]}
            </span>
          </div>

          {/* ok=false → surface the error openly; do NOT render as a quiet "clear" */}
          {!result.ok && (
            <p className="flex items-center gap-1.5 text-sm text-red-300 bg-red-900/30 rounded px-2 py-1.5" role="alert">
              <FileWarning size={14} aria-hidden="true" />
              {result.error ?? SCREEN_RESULT_ERROR}
            </p>
          )}

          {result.ok && (
            <>
              <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-content-muted">{SCREEN_WAGON_TYPE}</dt>
                <dd className="text-content-primary">{result.wagon_type || "—"}</dd>
                <dt className="text-content-muted">{SCREEN_MAIN_CARGO}</dt>
                <dd className="text-content-primary">{result.main_cargo || "—"}</dd>
              </dl>

              {/* Flags */}
              <div>
                <h4 className="text-xs font-semibold text-content-muted uppercase tracking-wide mb-1">
                  {SCREEN_FLAGS_TITLE}
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {flagEntries.map(([key, value]) => {
                    const ui = SCREEN_FLAG_UI[value];
                    return (
                      <span
                        key={key}
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border ${ui.cls}`}
                      >
                        {ui.icon}
                        {SCREEN_FLAG_NAME[key]}: {SCREEN_FLAG_VALUE[value]}
                      </span>
                    );
                  })}
                </div>
              </div>

              {/* Qwen summary */}
              {result.summary_uz && (
                <div>
                  <h4 className="text-xs font-semibold text-content-muted uppercase tracking-wide mb-1">
                    {SCREEN_SUMMARY_LABEL}
                  </h4>
                  <p className="text-sm text-content-secondary leading-snug whitespace-pre-line">
                    {result.summary_uz}
                  </p>
                </div>
              )}
            </>
          )}

          <p className="mt-auto text-xs text-content-muted font-mono">
            {SCREEN_SECONDS}: {result.seconds.toFixed(1)}s
          </p>
        </div>
      </div>
    </li>
  );
}
