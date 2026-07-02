import {
  useState, useRef, useEffect, useCallback, type MouseEvent,
} from "react";
import { Pencil } from "lucide-react";
import type {
  Detection, ImageFrame, OperatorAnnotation, ThreatCategory, PixelBox,
} from "../lib/types";
import { frameImageUrl } from "../lib/api";
import { catColor, hexA } from "../lib/theme";
import {
  VIEWER_LOADING, VIEWER_NO_IMAGE,
  VIEWER_DRAW_HINT, VIEWER_DRAW_CANCEL,
  VIEWER_ANALYZING_OVERLAY, THREAT_CATEGORY,
  MISSED_CATEGORY, MISSED_NOTE, MISSED_SAVE,
} from "../lib/uz";
import { IS_MOCK } from "../lib/mock";

interface Props {
  scanId:          string;
  frame:           ImageFrame | null;
  detections:      Detection[];
  selectedId:      string | null;
  onSelect:        (id: string | null) => void;
  analyzing?:      boolean;
  onAddAnnotation: (a: Omit<OperatorAnnotation, "note_uz"> & { note_uz: string | null }) => void;
}

interface DragState {
  startX: number; startY: number; curX: number; curY: number;
}

const CATEGORIES: ThreatCategory[] = [
  "firearm", "explosive", "bladed_weapon", "narcotics",
  "currency", "organic_anomaly", "metallic_anomaly", "contraband_other", "unknown",
];

// Scale image-space coords to display space.
function scaleBox(box: PixelBox, imgW: number, imgH: number, dispW: number, dispH: number) {
  const sx = dispW / imgW, sy = dispH / imgH;
  return { x: box.x * sx, y: box.y * sy, width: box.width * sx, height: box.height * sy };
}

export function XRayViewer({
  scanId, frame, detections, selectedId, onSelect, analyzing, onAddAnnotation,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef       = useRef<HTMLImageElement>(null);
  const [imgSize, setImgSize]     = useState({ w: 0, h: 0 });
  const [dispSize, setDispSize]   = useState({ w: 0, h: 0 });
  const [zoom, setZoom]           = useState(1);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError]   = useState(false);

  // Draw mode
  const [drawMode, setDrawMode]       = useState(false);
  const [drag, setDrag]               = useState<DragState | null>(null);
  const [pendingBox, setPendingBox]   = useState<PixelBox | null>(null);
  const [pendingCat, setPendingCat]   = useState<ThreatCategory>("unknown");
  const [pendingNote, setPendingNote] = useState("");

  const imgUrl = frame
    ? IS_MOCK
      ? `https://placehold.co/${frame.width_px}x${frame.height_px}/0d1119/3a4a63?text=X-RAY+MOCK`
      : frameImageUrl(scanId, frame.frame_id)
    : null;

  const measureDisp = useCallback(() => {
    const el = imgRef.current;
    if (!el) return;
    setDispSize({ w: el.clientWidth, h: el.clientHeight });
  }, []);

  useEffect(() => {
    const obs = new ResizeObserver(measureDisp);
    if (imgRef.current) obs.observe(imgRef.current);
    return () => obs.disconnect();
  }, [measureDisp]);

  const handleImgLoad = () => {
    const el = imgRef.current;
    if (!el) return;
    setImgSize({ w: el.naturalWidth, h: el.naturalHeight });
    setDispSize({ w: el.clientWidth, h: el.clientHeight });
    setImgLoaded(true);
    setImgError(false);
  };

  // Reset on frame change
  useEffect(() => {
    setImgLoaded(false);
    setImgError(false);
    setZoom(1);
    setDrawMode(false);
    setPendingBox(null);
  }, [frame?.frame_id]);

  // Zoom (integer 1×–4× to match the prototype controls)
  const clampZoom = (z: number) => Math.min(4, Math.max(1, z));
  const handleWheel = (e: React.WheelEvent) => {
    if (!drawMode) { e.preventDefault(); setZoom((z) => clampZoom(z - Math.sign(e.deltaY))); }
  };

  // Draw mode — mouse events
  function svgPoint(e: MouseEvent<SVGSVGElement>): { x: number; y: number } {
    const svg = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - svg.left, y: e.clientY - svg.top };
  }
  const handleMouseDown = (e: MouseEvent<SVGSVGElement>) => {
    if (!drawMode || e.button !== 0) return;
    const p = svgPoint(e);
    setDrag({ startX: p.x, startY: p.y, curX: p.x, curY: p.y });
    setPendingBox(null);
    e.preventDefault();
  };
  const handleMouseMove = (e: MouseEvent<SVGSVGElement>) => {
    if (!drag) return;
    const p = svgPoint(e);
    setDrag((d) => d ? { ...d, curX: p.x, curY: p.y } : null);
  };
  const handleMouseUp = () => {
    if (!drag || !frame || imgSize.w === 0) return;
    const { startX, startY, curX, curY } = drag;
    const minX = Math.min(startX, curX), minY = Math.min(startY, curY);
    const boxW = Math.abs(curX - startX), boxH = Math.abs(curY - startY);
    if (boxW < 4 || boxH < 4) { setDrag(null); return; }
    const sx = imgSize.w / dispSize.w, sy = imgSize.h / dispSize.h;
    setPendingBox({
      x: Math.round(minX * sx), y: Math.round(minY * sy),
      width: Math.round(boxW * sx), height: Math.round(boxH * sy),
    });
    setDrag(null);
  };

  const confirmAnnotation = () => {
    if (!pendingBox || !frame) return;
    onAddAnnotation({
      frame_id: frame.frame_id, box: pendingBox,
      category: pendingCat, note_uz: pendingNote || null,
    });
    setPendingBox(null);
    setPendingNote("");
    setDrawMode(false);
  };

  const dragRect = drag ? {
    x: Math.min(drag.startX, drag.curX), y: Math.min(drag.startY, drag.curY),
    width: Math.abs(drag.curX - drag.startX), height: Math.abs(drag.curY - drag.startY),
  } : null;

  const pendingDisp = pendingBox && imgSize.w
    ? scaleBox(pendingBox, imgSize.w, imgSize.h, dispSize.w, dispSize.h)
    : null;

  const frameLabel = frame?.view_label ?? "high_energy";

  return (
    <div className="flex flex-col" style={{ gap: 10 }}>
      {/* Toolbar: zoom group + draw toggle */}
      <div className="flex items-center justify-end" style={{ gap: 8 }}>
        <div className="flex items-center" style={{ gap: 2, background: "rgba(0,0,0,0.25)", borderRadius: 9, padding: 3 }}>
          <button onClick={() => setZoom((z) => clampZoom(z - 1))} aria-label="Kichiklashtirish"
            style={{ width: 28, height: 26, border: "none", borderRadius: 7, background: "transparent", color: "#aebbcf", fontSize: 16, cursor: "pointer" }}>−</button>
          <span className="font-mono" style={{ fontSize: 12, color: "#cbd5e1", width: 30, textAlign: "center" }}>{zoom}×</span>
          <button onClick={() => setZoom((z) => clampZoom(z + 1))} aria-label="Kattalashtirish"
            style={{ width: 28, height: 26, border: "none", borderRadius: 7, background: "transparent", color: "#aebbcf", fontSize: 16, cursor: "pointer" }}>+</button>
        </div>
        <button
          onClick={() => { setDrawMode((d) => !d); setPendingBox(null); }}
          aria-pressed={drawMode}
          className="inline-flex items-center"
          style={{
            gap: 6, padding: "7px 12px", fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: "pointer",
            border: `1px solid ${drawMode ? "rgba(245,158,11,0.6)" : "rgba(255,255,255,0.12)"}`,
            background: drawMode ? "rgba(245,158,11,0.15)" : "rgba(255,255,255,0.03)",
            color: drawMode ? "#fbbf24" : "#aebbcf",
          }}
        >
          <Pencil size={14} aria-hidden="true" />Chizish
        </button>
      </div>

      {/* Viewer area */}
      <div
        ref={containerRef}
        onWheel={handleWheel}
        style={{
          position: "relative", height: 452, borderRadius: 14, overflow: "hidden",
          border: "1px solid rgba(255,255,255,0.1)",
          background: "radial-gradient(circle at 50% 42%,#0d1119,#05070c)",
        }}
      >
        {/* Frame badge */}
        <div className="font-mono" style={{
          position: "absolute", top: 10, left: 12, zIndex: 30, display: "flex", alignItems: "center", gap: 7,
          fontSize: 10.5, color: "#5b6679", background: "rgba(0,0,0,0.35)", padding: "3px 9px", borderRadius: 7, pointerEvents: "none",
        }}>{frameLabel}</div>

        {/* Loading / error / empty */}
        {!imgLoaded && !imgError && imgUrl && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="animate-pulse" style={{ color: "#8595ad", fontSize: 13 }}>{VIEWER_LOADING}</span>
          </div>
        )}
        {(imgError || !imgUrl) && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span style={{ color: "#5b6679", fontSize: 13 }}>{VIEWER_NO_IMAGE}</span>
          </div>
        )}

        {/* Analyzing overlay */}
        {analyzing && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center animate-pulse" style={{ gap: 14, background: "rgba(5,7,12,0.55)" }}>
            <span style={{ width: 40, height: 40, border: "3px solid rgba(148,163,184,0.25)", borderTopColor: "#94a3b8", borderRadius: 999 }} className="animate-spin" />
            <span style={{ fontSize: 13, color: "#8595ad" }}>{VIEWER_ANALYZING_OVERLAY}</span>
          </div>
        )}

        {/* Zoomable image + boxes */}
        <div className="w-full h-full flex items-center justify-center" style={{ overflow: "hidden" }}>
          <div style={{ transform: `scale(${zoom})`, transformOrigin: "center", transition: "transform 0.2s" }}>
            {imgUrl && (
              <div className="relative" style={{ display: "inline-block" }}>
                <img
                  ref={imgRef}
                  src={imgUrl}
                  alt="Skanerlangan tasvir"
                  onLoad={handleImgLoad}
                  onError={() => setImgError(true)}
                  draggable={false}
                  className="block select-none"
                  style={{ maxWidth: "100%", maxHeight: 452, imageRendering: "pixelated", opacity: imgLoaded ? 1 : 0 }}
                />

                {imgLoaded && dispSize.w > 0 && (
                  <svg
                    width={dispSize.w}
                    height={dispSize.h}
                    viewBox={`0 0 ${dispSize.w} ${dispSize.h}`}
                    className="absolute inset-0"
                    style={{ cursor: drawMode ? "crosshair" : "default" }}
                    onMouseDown={handleMouseDown}
                    onMouseMove={handleMouseMove}
                    onMouseUp={handleMouseUp}
                    onMouseLeave={handleMouseUp}
                    aria-label="Aniqlangan buyumlar"
                  >
                    {detections.map((d) => {
                      const b = scaleBox(d.box, imgSize.w, imgSize.h, dispSize.w, dispSize.h);
                      const color = catColor(d.category);
                      const isSelected = d.detection_id === selectedId;
                      return (
                        <g
                          key={d.detection_id}
                          role="button"
                          tabIndex={0}
                          aria-label={`${THREAT_CATEGORY[d.category]} — ${Math.round(d.score * 100)}%`}
                          onClick={() => onSelect(isSelected ? null : d.detection_id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(isSelected ? null : d.detection_id); }
                          }}
                          style={{ cursor: "pointer", filter: `drop-shadow(0 0 ${isSelected ? 10 : 5}px ${hexA(color, isSelected ? 0.85 : 0.45)})` }}
                        >
                          <rect
                            x={b.x} y={b.y} width={b.width} height={b.height}
                            fill={hexA(color, isSelected ? 0.2 : 0.12)}
                            stroke={color}
                            strokeWidth={isSelected ? 2.4 : 1.8}
                            rx={3}
                          />
                          <rect x={b.x} y={b.y - 15} width={Math.min(Math.max(b.width, 56), 150)} height={14} fill="rgba(5,7,12,0.82)" rx={4} />
                          <text x={b.x + 5} y={b.y - 4} fill={color} fontSize={9} fontFamily="'IBM Plex Mono', monospace" fontWeight={600}>
                            {THREAT_CATEGORY[d.category]} {Math.round(d.score * 100)}%
                          </text>
                        </g>
                      );
                    })}

                    {dragRect && (
                      <rect x={dragRect.x} y={dragRect.y} width={dragRect.width} height={dragRect.height}
                        fill="rgba(245,158,11,0.14)" stroke="#fbbf24" strokeWidth={1.6} strokeDasharray="4 2" rx={3} />
                    )}
                    {pendingDisp && (
                      <rect x={pendingDisp.x} y={pendingDisp.y} width={pendingDisp.width} height={pendingDisp.height}
                        fill="rgba(245,158,11,0.10)" stroke="#fbbf24" strokeWidth={1.6} strokeDasharray="4 2" rx={3} />
                    )}
                  </svg>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Draw hint */}
      <div className="flex items-center" style={{ fontSize: 11.5, color: "#5b6679", gap: 7 }}>
        <span style={{ width: 6, height: 6, borderRadius: 2, background: "#fbbf24" }} aria-hidden="true" />
        Chizish rejimida detektor o'tkazib yuborgan sohani belgilang.
      </div>

      {/* Annotation form — shown after drawing a box */}
      {pendingBox && (
        <div className="animate-slide-in-right" style={{ borderRadius: 12, border: "1px solid rgba(245,158,11,0.4)", background: "rgba(245,158,11,0.08)", padding: 12 }}>
          <p style={{ fontSize: 12.5, fontWeight: 600, color: "#fbbf24", marginBottom: 8 }}>{VIEWER_DRAW_HINT}</p>
          <div className="flex flex-wrap" style={{ gap: 8 }}>
            <div className="flex-1" style={{ minWidth: 140 }}>
              <label className="block" style={{ fontSize: 11, color: "#aebbcf", marginBottom: 4 }}>{MISSED_CATEGORY}</label>
              <select
                value={pendingCat}
                onChange={(e) => setPendingCat(e.target.value as ThreatCategory)}
                style={{ width: "100%", padding: "7px 9px", borderRadius: 8, background: "rgba(0,0,0,0.3)", border: "1px solid rgba(245,158,11,0.4)", color: "#e2e8f0", fontSize: 12.5 }}
              >
                {CATEGORIES.map((c) => <option key={c} value={c}>{THREAT_CATEGORY[c]}</option>)}
              </select>
            </div>
            <div className="flex-1" style={{ minWidth: 140 }}>
              <label className="block" style={{ fontSize: 11, color: "#aebbcf", marginBottom: 4 }}>{MISSED_NOTE}</label>
              <input
                type="text"
                value={pendingNote}
                onChange={(e) => setPendingNote(e.target.value)}
                maxLength={200}
                style={{ width: "100%", padding: "7px 9px", borderRadius: 8, background: "rgba(0,0,0,0.25)", border: "1px solid rgba(255,255,255,0.12)", color: "#e2e8f0", fontSize: 12.5 }}
              />
            </div>
          </div>
          <div className="flex" style={{ gap: 8, marginTop: 10 }}>
            <button onClick={confirmAnnotation}
              style={{ padding: "8px 14px", borderRadius: 9, fontSize: 12.5, fontWeight: 600, border: "1px solid #f59e0b", background: "rgba(245,158,11,0.2)", color: "#fbbf24", cursor: "pointer" }}>
              {MISSED_SAVE}
            </button>
            <button onClick={() => setPendingBox(null)}
              style={{ padding: "8px 14px", borderRadius: 9, fontSize: 12.5, fontWeight: 600, border: "1px solid rgba(255,255,255,0.14)", background: "rgba(255,255,255,0.04)", color: "#aebbcf", cursor: "pointer" }}>
              {VIEWER_DRAW_CANCEL}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
