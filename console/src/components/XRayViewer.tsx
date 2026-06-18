import {
  useState, useRef, useEffect, useCallback, type MouseEvent,
} from "react";
import { ZoomIn, ZoomOut, Maximize2, Pencil, X } from "lucide-react";
import type {
  Detection, ImageFrame, OperatorAnnotation, ThreatCategory, PixelBox,
} from "../lib/types";
import { frameImageUrl } from "../lib/api";
import {
  VIEWER_TITLE, VIEWER_LOADING, VIEWER_NO_IMAGE,
  VIEWER_ZOOM_IN, VIEWER_ZOOM_OUT, VIEWER_ZOOM_RESET,
  VIEWER_DRAW_MODE, VIEWER_DRAW_HINT, VIEWER_DRAW_CANCEL,
  VIEWER_ANALYZING_OVERLAY, THREAT_CATEGORY,
  MISSED_CATEGORY, MISSED_NOTE, MISSED_SAVE,
} from "../lib/uz";
import { IS_MOCK } from "../lib/mock";

// ------------------------------------------------------------------
// Risk colours per category (for bounding box stroke)
// ------------------------------------------------------------------
const CATEGORY_COLOR: Record<ThreatCategory, string> = {
  firearm:          "#ef4444",
  explosive:        "#ef4444",
  bladed_weapon:    "#f97316",
  narcotics:        "#a855f7",
  currency:         "#eab308",
  organic_anomaly:  "#22d3ee",
  metallic_anomaly: "#94a3b8",
  contraband_other: "#f59e0b",
  unknown:          "#64748b",
};

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------
interface Props {
  scanId:           string;
  frame:            ImageFrame | null;
  detections:       Detection[];
  selectedId:       string | null;
  onSelect:         (id: string | null) => void;
  analyzing?:       boolean;
  onAddAnnotation:  (a: Omit<OperatorAnnotation, "note_uz"> & { note_uz: string | null }) => void;
}

interface DragState {
  startX: number; startY: number; curX: number; curY: number;
}

const CATEGORIES: ThreatCategory[] = [
  "firearm", "explosive", "bladed_weapon", "narcotics",
  "currency", "organic_anomaly", "metallic_anomaly", "contraband_other", "unknown",
];

// ------------------------------------------------------------------
// Helper: scale image-space coords to display space
// ------------------------------------------------------------------
function scaleBox(
  box: PixelBox,
  imgW: number, imgH: number,
  dispW: number, dispH: number,
) {
  const sx = dispW / imgW;
  const sy = dispH / imgH;
  return {
    x:      box.x * sx,
    y:      box.y * sy,
    width:  box.width  * sx,
    height: box.height * sy,
  };
}

// ------------------------------------------------------------------
// Main component
// ------------------------------------------------------------------
export function XRayViewer({
  scanId, frame, detections, selectedId, onSelect, analyzing, onAddAnnotation,
}: Props) {
  const containerRef    = useRef<HTMLDivElement>(null);
  const imgRef          = useRef<HTMLImageElement>(null);
  const [imgSize, setImgSize]     = useState({ w: 0, h: 0 });
  const [dispSize, setDispSize]   = useState({ w: 0, h: 0 });
  const [zoom, setZoom]           = useState(1);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError]   = useState(false);

  // Draw mode
  const [drawMode, setDrawMode]   = useState(false);
  const [drag, setDrag]           = useState<DragState | null>(null);
  const [pendingBox, setPendingBox] = useState<PixelBox | null>(null);
  const [pendingCat, setPendingCat] = useState<ThreatCategory>("unknown");
  const [pendingNote, setPendingNote] = useState("");

  // --------------------
  // Image URL
  // --------------------
  const imgUrl = frame
    ? IS_MOCK
      ? `https://placehold.co/${frame.width_px}x${frame.height_px}/1a1d27/475569?text=X-RAY+MOCK`
      : frameImageUrl(scanId, frame.frame_id)
    : null;

  // --------------------
  // Measure displayed image dimensions
  // --------------------
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
    setDispSize({ w: el.clientWidth,  h: el.clientHeight });
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

  // --------------------
  // Zoom
  // --------------------
  const clampZoom = (z: number) => Math.min(4, Math.max(0.5, z));
  const handleWheel = (e: React.WheelEvent) => {
    if (!drawMode) {
      e.preventDefault();
      setZoom((z) => clampZoom(z - e.deltaY * 0.001));
    }
  };

  // --------------------
  // Draw mode — mouse events
  // --------------------
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
    const minX = Math.min(startX, curX);
    const minY = Math.min(startY, curY);
    const boxW = Math.abs(curX - startX);
    const boxH = Math.abs(curY - startY);
    if (boxW < 4 || boxH < 4) { setDrag(null); return; }

    // Convert display coords → image coords
    const sx = imgSize.w / dispSize.w;
    const sy = imgSize.h / dispSize.h;
    setPendingBox({
      x:      Math.round(minX * sx),
      y:      Math.round(minY * sy),
      width:  Math.round(boxW * sx),
      height: Math.round(boxH * sy),
    });
    setDrag(null);
  };

  const confirmAnnotation = () => {
    if (!pendingBox || !frame) return;
    onAddAnnotation({
      frame_id: frame.frame_id,
      box:      pendingBox,
      category: pendingCat,
      note_uz:  pendingNote || null,
    });
    setPendingBox(null);
    setPendingNote("");
    setDrawMode(false);
  };

  // --------------------
  // Drag rect in display space
  // --------------------
  const dragRect = drag
    ? {
        x:      Math.min(drag.startX, drag.curX),
        y:      Math.min(drag.startY, drag.curY),
        width:  Math.abs(drag.curX - drag.startX),
        height: Math.abs(drag.curY - drag.startY),
      }
    : null;

  // Pending box in display space
  const pendingDisp = pendingBox && imgSize.w
    ? scaleBox(pendingBox, imgSize.w, imgSize.h, dispSize.w, dispSize.h)
    : null;

  // --------------------
  // Render
  // --------------------
  return (
    <div className="flex flex-col h-full gap-2">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-content-primary">{VIEWER_TITLE}</h2>
        <div className="flex items-center gap-1">
          <ToolBtn onClick={() => setZoom((z) => clampZoom(z + 0.2))} title={VIEWER_ZOOM_IN}>
            <ZoomIn size={14} />
          </ToolBtn>
          <ToolBtn onClick={() => setZoom((z) => clampZoom(z - 0.2))} title={VIEWER_ZOOM_OUT}>
            <ZoomOut size={14} />
          </ToolBtn>
          <ToolBtn onClick={() => setZoom(1)} title={VIEWER_ZOOM_RESET}>
            <Maximize2 size={14} />
          </ToolBtn>
          <div className="w-px h-4 bg-surface-border mx-0.5" />
          <ToolBtn
            onClick={() => { setDrawMode((d) => !d); setPendingBox(null); }}
            title={drawMode ? VIEWER_DRAW_CANCEL : VIEWER_DRAW_MODE}
            active={drawMode}
          >
            {drawMode ? <X size={14} /> : <Pencil size={14} />}
          </ToolBtn>
        </div>
      </div>

      {drawMode && !pendingBox && (
        <p className="text-xs text-amber-400 animate-fade-in">{VIEWER_DRAW_HINT}</p>
      )}

      {/* Viewer area */}
      <div
        ref={containerRef}
        className="relative flex-1 overflow-hidden rounded-lg bg-black border border-surface-border"
        onWheel={handleWheel}
      >
        {/* Loading skeleton */}
        {!imgLoaded && !imgError && imgUrl && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-content-muted text-sm animate-pulse">{VIEWER_LOADING}</span>
          </div>
        )}

        {/* Error */}
        {imgError && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-content-muted text-sm">{VIEWER_NO_IMAGE}</span>
          </div>
        )}

        {/* No frame */}
        {!imgUrl && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-content-muted text-sm">{VIEWER_NO_IMAGE}</span>
          </div>
        )}

        {/* Analyzing overlay */}
        {analyzing && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/60 rounded-lg">
            <span className="text-blue-300 text-sm font-medium animate-pulse">
              {VIEWER_ANALYZING_OVERLAY}
            </span>
          </div>
        )}

        {/* Zoomable container */}
        <div
          className="w-full h-full flex items-center justify-center"
          style={{ overflow: "hidden" }}
        >
          <div style={{ transform: `scale(${zoom})`, transformOrigin: "center", transition: "transform 0.15s" }}>
            {imgUrl && (
              <div className="relative" style={{ display: "inline-block" }}>
                {/* X-ray image */}
                <img
                  ref={imgRef}
                  src={imgUrl}
                  alt="Skanerlangan tasvir"
                  onLoad={handleImgLoad}
                  onError={() => setImgError(true)}
                  draggable={false}
                  className="block max-w-full max-h-full select-none"
                  style={{ imageRendering: "pixelated", opacity: imgLoaded ? 1 : 0 }}
                />

                {/* SVG overlay for boxes */}
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
                    {/* Detection boxes */}
                    {detections.map((d) => {
                      const b = scaleBox(d.box, imgSize.w, imgSize.h, dispSize.w, dispSize.h);
                      const color = CATEGORY_COLOR[d.category];
                      const isSelected = d.detection_id === selectedId;
                      return (
                        <g
                          key={d.detection_id}
                          role="button"
                          tabIndex={0}
                          aria-label={`${THREAT_CATEGORY[d.category]} — ${Math.round(d.score * 100)}%`}
                          onClick={() => onSelect(isSelected ? null : d.detection_id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onSelect(isSelected ? null : d.detection_id);
                            }
                          }}
                          style={{ cursor: "pointer" }}
                        >
                          <rect
                            x={b.x} y={b.y} width={b.width} height={b.height}
                            fill={isSelected ? `${color}22` : "none"}
                            stroke={color}
                            strokeWidth={isSelected ? 2.5 : 1.5}
                            strokeDasharray={isSelected ? undefined : "6 2"}
                            rx={2}
                          />
                          {/* Label */}
                          <rect
                            x={b.x} y={b.y - 18}
                            width={Math.min(b.width, 130)} height={16}
                            fill={color} rx={2}
                            opacity={0.92}
                          />
                          <text
                            x={b.x + 4} y={b.y - 5}
                            fill="#fff" fontSize={10} fontFamily="Inter, sans-serif"
                            fontWeight={600}
                          >
                            {THREAT_CATEGORY[d.category]} {Math.round(d.score * 100)}%
                          </text>
                          {/* Selection pulse ring */}
                          {isSelected && (
                            <rect
                              x={b.x - 2} y={b.y - 2}
                              width={b.width + 4} height={b.height + 4}
                              fill="none" stroke={color} strokeWidth={1}
                              opacity={0.4} rx={4}
                              className="animate-pulse-fast"
                            />
                          )}
                        </g>
                      );
                    })}

                    {/* Live drag rect */}
                    {dragRect && (
                      <rect
                        x={dragRect.x} y={dragRect.y}
                        width={dragRect.width} height={dragRect.height}
                        fill="rgba(251,191,36,0.1)"
                        stroke="#fbbf24" strokeWidth={1.5} strokeDasharray="4 2"
                        rx={2}
                      />
                    )}

                    {/* Confirmed pending annotation */}
                    {pendingDisp && (
                      <rect
                        x={pendingDisp.x} y={pendingDisp.y}
                        width={pendingDisp.width} height={pendingDisp.height}
                        fill="rgba(251,191,36,0.15)"
                        stroke="#fbbf24" strokeWidth={2}
                        rx={2}
                      />
                    )}
                  </svg>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Annotation form — shown after drawing a box */}
      {pendingBox && (
        <div className="animate-slide-in rounded-lg border border-amber-800/60 bg-amber-900/20 p-3 space-y-2">
          <p className="text-xs font-semibold text-amber-300">{VIEWER_DRAW_HINT}</p>
          <div className="flex gap-2 flex-wrap">
            <div className="flex-1 min-w-[140px]">
              <label className="block text-xs text-content-secondary mb-1">{MISSED_CATEGORY}</label>
              <select
                className="w-full bg-surface-card border border-surface-border rounded px-2 py-1 text-xs text-content-primary focus:outline-none focus:ring-1 focus:ring-amber-500"
                value={pendingCat}
                onChange={(e) => setPendingCat(e.target.value as ThreatCategory)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>{THREAT_CATEGORY[c]}</option>
                ))}
              </select>
            </div>
            <div className="flex-1 min-w-[140px]">
              <label className="block text-xs text-content-secondary mb-1">{MISSED_NOTE}</label>
              <input
                type="text"
                className="w-full bg-surface-card border border-surface-border rounded px-2 py-1 text-xs text-content-primary placeholder-content-muted focus:outline-none focus:ring-1 focus:ring-amber-500"
                value={pendingNote}
                onChange={(e) => setPendingNote(e.target.value)}
                maxLength={200}
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={confirmAnnotation}
              className="px-3 py-1.5 rounded text-xs font-semibold bg-amber-600 hover:bg-amber-500 text-white transition-colors"
            >
              {MISSED_SAVE}
            </button>
            <button
              onClick={() => setPendingBox(null)}
              className="px-3 py-1.5 rounded text-xs text-content-secondary hover:text-content-primary transition-colors"
            >
              {VIEWER_DRAW_CANCEL}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------
// Small toolbar button
// ------------------------------------------------------------------
function ToolBtn({
  children, onClick, title, active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      aria-pressed={active}
      className={`p-1.5 rounded transition-colors ${
        active
          ? "bg-amber-600/30 text-amber-300 border border-amber-600/40"
          : "text-content-secondary hover:text-content-primary hover:bg-surface-hover"
      }`}
    >
      {children}
    </button>
  );
}
