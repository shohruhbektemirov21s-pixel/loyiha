import { useCallback, useEffect, useRef, useState } from "react";
import { ImageUp, Loader2, X, AlertTriangle } from "lucide-react";
import type { ScreenResult, ScreenFlag } from "../lib/types";
import { screenImages, ApiError } from "../lib/api";
import {
  SCREEN_DROPZONE, SCREEN_DROPZONE_HINT, SCREEN_PICK_FILES,
  SCREEN_ANALYZE, SCREEN_ANALYZING, SCREEN_ANALYZING_HINT,
  SCREEN_CLEAR_ALL, SCREEN_REMOVE_FILE,
  SCREEN_WAGON_TYPE, SCREEN_MAIN_CARGO,
  SCREEN_SECONDS, SCREEN_RESULT_ERROR,
  SCREEN_ERROR, SCREEN_UNSUPPORTED, SCREEN_DISCLAIMER, SCREEN_FLAG_NAME,
  SCREEN_FLAG_VALUE, RISK_BAND_SHORT, MODE_UPLOAD,
} from "../lib/uz";
import { bandColor, bandBg, FLAG_COLOR, hexA } from "../lib/theme";

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
      style={{ maxWidth: 1080, margin: "0 auto", width: "100%" }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
          marginBottom: 16,
        }}
      >
        <div>
          <p
            style={{
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.14em",
              color: "#7c8aa3",
              fontWeight: 600,
              margin: 0,
            }}
          >
            Skrining
          </p>
          <h2
            id="screen-heading"
            style={{
              fontSize: 19,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              color: "#e6edf6",
              margin: "4px 0 0",
            }}
          >
            {MODE_UPLOAD}
          </h2>
        </div>

        <button
          onClick={() => inputRef.current?.click()}
          disabled={analyzing}
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "#062a26",
            padding: "9px 16px",
            borderRadius: 9,
            background: "linear-gradient(135deg,#2dd4bf,#14b8a6)",
            border: "none",
            cursor: analyzing ? "default" : "pointer",
            opacity: analyzing ? 0.5 : 1,
            whiteSpace: "nowrap",
          }}
        >
          + Rasm qo'shish
        </button>
      </div>

      {/* Dropzone */}
      <div
        role="button"
        tabIndex={0}
        aria-label={SCREEN_PICK_FILES}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 10,
          padding: 34,
          borderRadius: 14,
          textAlign: "center",
          border: `1.5px dashed ${dragOver ? "rgba(129,140,248,0.7)" : "rgba(255,255,255,0.16)"}`,
          background: dragOver ? "rgba(99,102,241,0.10)" : "rgba(255,255,255,0.02)",
          transition: "all .15s",
          cursor: "pointer",
        }}
      >
        <ImageUp size={30} stroke="#6b7a93" aria-hidden="true" />
        <p style={{ fontSize: 14, color: "#aebbcf", fontWeight: 500, margin: 0 }}>
          {SCREEN_DROPZONE}
        </p>
        <p style={{ fontSize: 12, color: "#5b6679", margin: 0 }}>
          {SCREEN_DROPZONE_HINT}
        </p>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        style={{ display: "none" }}
        onChange={onInputChange}
        aria-label={SCREEN_PICK_FILES}
      />

      {/* Action row: analyze + clear */}
      {pending.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 14, flexWrap: "wrap" }}>
          <button
            onClick={handleAnalyze}
            disabled={analyzing}
            aria-busy={analyzing}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 7,
              fontSize: 13,
              fontWeight: 600,
              color: "#062a26",
              padding: "9px 16px",
              borderRadius: 9,
              background: "linear-gradient(135deg,#2dd4bf,#14b8a6)",
              border: "none",
              cursor: analyzing ? "default" : "pointer",
              opacity: analyzing ? 0.7 : 1,
            }}
          >
            {analyzing
              ? <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              : <ImageUp size={14} aria-hidden="true" />}
            {analyzing ? SCREEN_ANALYZING : SCREEN_ANALYZE}
          </button>

          <button
            onClick={clearAll}
            disabled={analyzing}
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: "#94a3b8",
              padding: "9px 14px",
              borderRadius: 9,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.12)",
              cursor: analyzing ? "default" : "pointer",
              opacity: analyzing ? 0.5 : 1,
            }}
          >
            {SCREEN_CLEAR_ALL}
          </button>

          {analyzing && (
            <span
              role="status"
              className="animate-pulse"
              style={{ fontSize: 12.5, color: "#5b6679" }}
            >
              {SCREEN_ANALYZING_HINT}
            </span>
          )}

          <span style={{ marginLeft: "auto", fontSize: 12, color: "#5b6679", fontFamily: "ui-monospace, monospace" }}>
            {pending.length} ta rasm
          </span>
        </div>
      )}

      {/* Pending uploads preview — operator yuklagan rasmlar tahlildan OLDIN
          shu yerda ko'rinadi (bir nechta rasm qo'llab-quvvatlanadi). Natija
          kelgach (results) bu grid o'rnini ResultCard'lar egallaydi. */}
      {pending.length > 0 && results.length === 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
            gap: 12,
            marginTop: 16,
          }}
        >
          {pending.map((p) => (
            <div
              key={p.id}
              style={{
                position: "relative",
                borderRadius: 12,
                overflow: "hidden",
                border: "1px solid rgba(255,255,255,0.09)",
                background: "rgba(255,255,255,0.03)",
              }}
            >
              <div style={{ position: "relative", height: 110, background: "#0b0e16" }}>
                <img
                  src={p.preview}
                  alt={p.file.name}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    opacity: analyzing ? 0.45 : 1,
                    transition: "opacity .15s",
                  }}
                />
                {analyzing && (
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Loader2 size={20} className="animate-spin" stroke="#2dd4bf" aria-hidden="true" />
                  </div>
                )}
                {!analyzing && (
                  <button
                    onClick={() => removeFile(p.id)}
                    aria-label={`${SCREEN_REMOVE_FILE}: ${p.file.name}`}
                    style={{
                      position: "absolute",
                      top: 6,
                      right: 6,
                      width: 24,
                      height: 24,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      borderRadius: 6,
                      color: "#cbd5e1",
                      background: "rgba(0,0,0,0.5)",
                      border: "1px solid rgba(255,255,255,0.15)",
                      cursor: "pointer",
                    }}
                  >
                    <X size={12} aria-hidden="true" />
                  </button>
                )}
              </div>
              <div style={{ padding: "7px 9px" }}>
                <div
                  title={p.file.name}
                  style={{
                    fontFamily: "ui-monospace, monospace",
                    fontSize: 11,
                    color: "#cbd5e1",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {p.file.name}
                </div>
                <div style={{ fontSize: 10, color: "#5b6679", marginTop: 2 }}>
                  {(p.file.size / 1024).toFixed(0)} KB
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Batch error banner */}
      {error && (
        <p
          role="alert"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginTop: 14,
            fontSize: 13,
            color: "#fca5a5",
            background: "rgba(239,68,68,0.10)",
            border: "1px solid rgba(239,68,68,0.30)",
            borderRadius: 10,
            padding: "9px 13px",
          }}
        >
          <AlertTriangle size={15} aria-hidden="true" />
          {error}
        </p>
      )}

      {/* Results grid */}
      {results.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))",
            gap: 14,
            marginTop: 16,
          }}
        >
          {results.map((r, i) => (
            <ResultCard
              key={`${r.filename}-${i}`}
              result={r}
              preview={previewFor(r.filename)}
              onRemove={() => {
                const target = pending.find((p) => p.file.name === r.filename);
                if (target) removeFile(target.id);
              }}
            />
          ))}
        </div>
      )}

      {/* Footer disclaimer note */}
      <p
        style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          marginTop: 14,
          fontSize: 11.5,
          color: "#5b6679",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: 7,
            height: 7,
            borderRadius: 999,
            background: "#64748b",
            flexShrink: 0,
          }}
        />
        {SCREEN_DISCLAIMER}
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// One result card per analysed image.
// ---------------------------------------------------------------------------
function ResultCard({
  result,
  preview,
  onRemove,
}: {
  result: ScreenResult;
  preview?: string;
  onRemove: () => void;
}) {
  const band = bandColor(result.risk_band);
  const flagEntries = [
    ["narcotics", result.flags.narcotics],
    ["weapon",    result.flags.weapon],
    ["tobacco",   result.flags.tobacco],
    ["other",     result.flags.other],
  ] as const;

  const mono = "ui-monospace, SFMono-Regular, Menlo, monospace";

  return (
    <div
      style={{
        background: "rgba(255,255,255,0.04)",
        border: "1px solid rgba(255,255,255,0.09)",
        borderRadius: 14,
        overflow: "hidden",
      }}
    >
      {/* Thumb area */}
      <div
        style={{
          position: "relative",
          height: 120,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background:
            "repeating-linear-gradient(135deg,rgba(255,255,255,0.04) 0 10px,rgba(255,255,255,0.015) 10px 20px), radial-gradient(circle at 50% 40%,rgba(255,255,255,0.06),transparent)",
        }}
      >
        {preview ? (
          <img
            src={preview}
            alt={result.filename}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          <span style={{ fontFamily: mono, fontSize: 11, color: "#5b6679" }}>
            RENTGEN TASVIRI
          </span>
        )}

        <button
          onClick={onRemove}
          aria-label={`${SCREEN_REMOVE_FILE}: ${result.filename}`}
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            width: 26,
            height: 26,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: 7,
            color: "#cbd5e1",
            background: "rgba(0,0,0,0.4)",
            border: "1px solid rgba(255,255,255,0.15)",
            cursor: "pointer",
          }}
        >
          <X size={13} aria-hidden="true" />
        </button>
      </div>

      {/* Body */}
      <div style={{ padding: "13px 14px" }}>
        {/* Top row: filename + risk band pill */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            marginBottom: 11,
          }}
        >
          <span
            title={result.filename}
            style={{
              fontFamily: mono,
              fontSize: 12.5,
              color: "#cbd5e1",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {result.filename}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              flexShrink: 0,
              fontSize: 12,
              fontWeight: 600,
              padding: "4px 11px",
              borderRadius: 999,
              color: band,
              background: bandBg(result.risk_band),
              border: `1px solid ${hexA(band, 0.4)}`,
            }}
          >
            {RISK_BAND_SHORT[result.risk_band]}
          </span>
        </div>

        {/* Per-image error */}
        {result.error && (
          <p
            role="alert"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              fontSize: 12.5,
              color: "#fca5a5",
              background: "rgba(239,68,68,0.10)",
              border: "1px solid rgba(239,68,68,0.28)",
              borderRadius: 9,
              padding: "8px 10px",
              marginBottom: 11,
            }}
          >
            <AlertTriangle size={13} aria-hidden="true" />
            {result.error ?? SCREEN_RESULT_ERROR}
          </p>
        )}

        {/* Two columns: wagon type / main cargo */}
        <div
          style={{
            display: "flex",
            gap: 16,
            marginBottom: 11,
            fontSize: 12,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 10.5, textTransform: "uppercase", color: "#6b7a93" }}>
              {SCREEN_WAGON_TYPE}
            </div>
            <div style={{ color: "#cbd5e1" }}>{result.wagon_type || "—"}</div>
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 10.5, textTransform: "uppercase", color: "#6b7a93" }}>
              {SCREEN_MAIN_CARGO}
            </div>
            <div style={{ color: "#cbd5e1" }}>{result.main_cargo || "—"}</div>
          </div>
        </div>

        {/* Flags grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 7,
            marginBottom: 8,
          }}
        >
          {flagEntries.map(([key, value]) => {
            const fc = FLAG_COLOR[value as ScreenFlag];
            return (
              <div
                key={key}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 3,
                  padding: "8px 10px",
                  borderRadius: 9,
                  background: hexA(fc, 0.10),
                  border: `1px solid ${hexA(fc, 0.32)}`,
                }}
              >
                <span style={{ fontSize: 10, textTransform: "uppercase", color: "#7c8aa3" }}>
                  {SCREEN_FLAG_NAME[key]}
                </span>
                <span style={{ fontFamily: mono, fontSize: 13, fontWeight: 600, color: fc }}>
                  {SCREEN_FLAG_VALUE[value as ScreenFlag]}
                </span>
              </div>
            );
          })}
        </div>

        {/* Summary */}
        {result.summary_uz && (
          <p
            style={{
              fontSize: 12.5,
              color: "#94a3b8",
              lineHeight: 1.5,
              marginBottom: 8,
              whiteSpace: "pre-line",
            }}
          >
            {result.summary_uz}
          </p>
        )}

        {/* Inference time */}
        <p style={{ fontFamily: mono, fontSize: 11, color: "#5b6679", margin: 0 }}>
          {SCREEN_SECONDS}: {Math.round(result.seconds * 1000)} ms
        </p>
      </div>
    </div>
  );
}
