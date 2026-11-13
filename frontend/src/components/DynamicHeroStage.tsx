import React, { useEffect, useRef } from 'react';
import { Compass, Eye, Shield, Sparkles, Swords } from 'lucide-react';
import { HEROES } from '../data/heroes';
import { DynamicHeroStageProps, HeroId, HeroRigData } from '../types/hero';
import { heroAssetCache } from '../utils/assetPreloader';
import { DPFrameInterpolator, KeyPose, TARGET_FPS } from '../engine/dpInterpolation';
import {
  POSE_FIELDS,
  REST_POSE,
  RigPose,
  SkinnedHeroMesh,
  buildChoreography,
  sanitizeRig,
} from '../engine/heroRig';
import { HeroGLRenderer } from '../engine/heroGLRenderer';
import { BackdropCamera, BackdropTheme, SceneBackdrop, resolveBackdropTheme } from '../engine/sceneBackdrop';

/** Chỉ nhận link http(s) cho dòng ghi nguồn (chặn javascript:, data:...). */
const safeHttpUrl = (value?: string): string | undefined => {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : undefined;
  } catch {
    return undefined;
  }
};
import { StreamingMouthSync, textSyllables } from '../engine/visemeSync';

/**
 * DynamicHeroStage — Phase 3B: động cơ dựng hình 60 FPS phía sau GlassChatBox.
 *
 * Mỗi khung (requestAnimationFrame):
 *  1. Cảnh nền động thủ tục (quê hương / Bạch Đằng / chiến trường / điện) + máy quay nội suy DP.
 *  2. Model anh hùng GỐC (/assets/heroes/{id}.png, lấy từ heroAssetCache) được dựng bằng WebGL qua lưới LBS:
 *     ảnh chỉ là kết cấu — không một điểm ảnh nào bị sửa; chỉ khung xương (thân, đầu, tay, hàm) chuyển động.
 *  3. FOREGROUND_TALKING: đứng tiền cảnh, khẩu hình = Viterbi DP trên viseme của luồng chữ đang stream
 *     (sceneData.activeChunk); isStreaming = false → miệng khép ngay lập tức.
 *     BACKGROUND_ACTION: toàn thân trong trận, tư thế chỉ huy (DP Frame Interpolation), tắt hoàn toàn khẩu hình.
 */

interface HeroResources {
  heroId: HeroId;
  source: HTMLImageElement | ImageBitmap;
  rigRef: HeroRigData | null | undefined;
  rig: HeroRigData;
  mesh: SkinnedHeroMesh;
  talk: RigPose[];
  action: RigPose[];
}

type LiveProps = DynamicHeroStageProps;

interface StageLayout {
  x0: number;
  y0: number;
  scale: number;
}

const MAX_DPR = 1.75;

/** Ảnh gốc có phải tranh đục toàn khung (4 góc không trong suốt)? Chỉ ĐỌC điểm ảnh, không sửa ảnh. */
function isOpaqueFrame(source: HTMLImageElement | ImageBitmap): boolean {
  try {
    const probe = document.createElement('canvas');
    probe.width = 16;
    probe.height = 16;
    const pctx = probe.getContext('2d', { willReadFrequently: true });
    if (!pctx) return false;
    pctx.drawImage(source, 0, 0, 16, 16);
    const data = pctx.getImageData(0, 0, 16, 16).data;
    const corners = [0, 15, 16 * 15, 16 * 16 - 1];
    return corners.every((index) => data[index * 4 + 3] > 230);
  } catch {
    return false;
  }
}
const TRANSITION_S = 0.7;

const smoothstep = (v: number) => {
  const x = Math.min(1, Math.max(0, v));
  return x * x * (3 - 2 * x);
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

function lerpPose(a: RigPose, b: RigPose, k: number): RigPose {
  const out = { ...a };
  for (const field of POSE_FIELDS) out[field] = a[field] + (b[field] - a[field]) * k;
  return out;
}

function samplePoses(frames: RigPose[], t: number): RigPose {
  if (!frames.length) return REST_POSE;
  const f = t * TARGET_FPS;
  const i = Math.floor(f);
  const n = frames.length;
  return lerpPose(frames[((i % n) + n) % n], frames[(((i + 1) % n) + n) % n], f - i);
}

/** Quỹ đạo máy quay vòng lặp (x, y, zoom) dựng bằng DP Frame Interpolation. */
function buildCamera(kind: 'talk' | 'action'): number[][] {
  const keys: KeyPose[] =
    kind === 'talk'
      ? [
          { values: [0, 0, 1], holdS: 1.2 },
          { values: [0.45, -0.2, 1.012], holdS: 0.8 },
          { values: [-0.35, 0.15, 1.006], holdS: 0.8 },
        ]
      : [
          { values: [0.1, 0, 1.02], holdS: 0.3 },
          { values: [0.85, 0.3, 1.045], holdS: 0.4 },
          { values: [-0.6, -0.25, 1.03], holdS: 0.3 },
          { values: [0.35, -0.4, 1.055], holdS: 0.3 },
        ];
  return new DPFrameInterpolator({ weights: [1, 1, 20], holdWeight: 4 }).run(keys, kind === 'talk' ? 12 : 8);
}

function sampleCamera(frames: number[][], t: number): number[] {
  const f = t * TARGET_FPS;
  const i = Math.floor(f);
  const n = frames.length;
  const a = frames[((i % n) + n) % n];
  const b = frames[(((i + 1) % n) + n) % n];
  return a.map((v, k) => v + (b[k] - v) * (f - i));
}

/** Bố cục: tiền cảnh trò chuyện (bán thân, lớn) hoặc toàn thân trong trận (nhỏ hơn, lệch trái). */
function computeLayout(rig: HeroRigData, mode: 'talk' | 'action', width: number, height: number): StageLayout {
  const j = rig.joints;
  const faceX = (rig.face_box[0] + rig.face_box[2]) / 2;
  const faceH = rig.face_box[3] - rig.face_box[1];
  const wide = width >= 1024;
  if (mode === 'talk') {
    const visibleBottom = Math.min(rig.height, Math.max(j.wrist_l[1], j.wrist_r[1], j.neck[1] + 2.5 * faceH) + 0.6 * faceH);
    const scale = Math.min((0.9 * height) / visibleBottom, (0.62 * width) / rig.width);
    const anchorX = wide ? 290 + 0.17 * width : width / 2;
    return { x0: anchorX - faceX * scale, y0: height - visibleBottom * scale, scale };
  }
  const scale = Math.min((0.74 * height) / rig.height, (0.5 * width) / rig.width);
  const anchorX = wide ? Math.max(0.28 * width, 290 + 0.08 * width) : width / 2;
  return { x0: anchorX - faceX * scale, y0: 0.97 * height - rig.height * scale, scale };
}

function hexToRgb(hex: string): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(hex.trim());
  if (!m) return '212,185,130';
  return `${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)}`;
}

export const DynamicHeroStage: React.FC<DynamicHeroStageProps> = (props) => {
  const { characterId, heroImageUrl, sceneMode, mouthSyncEnabled, isStreaming, sceneData } = props;
  const hero = HEROES[characterId];
  const isActionMode = sceneMode === 'BACKGROUND_ACTION';
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const liveRef = useRef<LiveProps>(props);
  const mouthRef = useRef<StreamingMouthSync>(new StreamingMouthSync());
  const consumedRef = useRef<string>('');

  liveRef.current = props;

  // ─── Khẩu hình: nạp phần chữ mới của luồng stream; tắt stream/tắt mouth sync → khép miệng ngay. ───
  const activeChunk = sceneData?.activeChunk ?? null;
  useEffect(() => {
    const mouth = mouthRef.current;
    if (!isStreaming || !mouthSyncEnabled || sceneMode !== 'FOREGROUND_TALKING') {
      mouth.stop();
      consumedRef.current = '';
      return;
    }
    if (!activeChunk || activeChunk.character_id !== characterId) return;
    const full = activeChunk.text;
    const consumed = consumedRef.current;
    const fresh = consumed && full.startsWith(consumed) ? full.slice(consumed.length) : full;
    if (consumed && !full.startsWith(consumed)) mouth.stop();
    consumedRef.current = full;
    if (!fresh.trim()) return;
    // Dùng khẩu hình DP của backend khi phần chữ mới đúng là cụm vừa gửi (cùng số âm tiết); nếu không, tự suy từ chữ.
    const hint =
      activeChunk.visemes && activeChunk.visemes.length && textSyllables(fresh).length === activeChunk.syllables
        ? activeChunk.visemes
        : undefined;
    mouth.push(fresh, performance.now(), hint);
  }, [activeChunk, isStreaming, mouthSyncEnabled, sceneMode, characterId]);

  // ─── Động cơ 60 FPS ───
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return undefined;
    const renderer = new HeroGLRenderer();
    const resources = new Map<HeroId, HeroResources>();
    const fallbackImages = new Map<string, HTMLImageElement>();
    const externalImages = new Map<string, HTMLImageElement>();
    const cameraTalk = buildCamera('talk');
    const cameraAction = buildCamera('action');
    const transition = document.createElement('canvas');
    const transitionCtx = transition.getContext('2d');
    let transitionStart = -Infinity;
    let backdrop: SceneBackdrop | null = null;
    let backdropKey = '';
    let modeBlend = liveRef.current.sceneMode === 'BACKGROUND_ACTION' ? 1 : 0;
    let energy = 0;
    let layout: StageLayout | null = null;
    let layoutHero: HeroId | null = null;
    let rafId = 0;
    let lastNow = performance.now();
    const epoch = lastNow;

    const loadImage = (cache: Map<string, HTMLImageElement>, url: string): HTMLImageElement => {
      let img = cache.get(url);
      if (!img) {
        img = new Image();
        img.decoding = 'async';
        img.src = url;
        cache.set(url, img);
      }
      return img;
    };

    const heroSource = (id: HeroId, url: string): HTMLImageElement | ImageBitmap | null => {
      // Ưu tiên HTMLImageElement đã decode (cờ nhân trước alpha của WebGL áp dụng chuẩn xác).
      const element = heroAssetCache.getImageElement(id);
      if (element && element.complete && element.naturalWidth > 0) return element;
      const bitmap = heroAssetCache.getBitmap(id);
      if (bitmap) return bitmap;
      const own = loadImage(fallbackImages, url);
      return own.complete && own.naturalWidth > 0 ? own : null;
    };

    const getResources = (live: LiveProps): HeroResources | null => {
      const id = live.characterId;
      const source = heroSource(id, live.heroImageUrl);
      if (!source) return null;
      const rigRef =
        (live.sceneData?.characterId === id ? live.sceneData?.rigData : null) ?? heroAssetCache.getStatus(id)?.rigData ?? null;
      const cached = resources.get(id);
      if (cached && cached.source === source && cached.rigRef === rigRef) return cached;
      const width = source instanceof HTMLImageElement ? source.naturalWidth : source.width;
      const height = source instanceof HTMLImageElement ? source.naturalHeight : source.height;
      const rig = sanitizeRig(rigRef, id, width, height);
      const built: HeroResources = {
        heroId: id,
        source,
        rigRef,
        rig,
        mesh: new SkinnedHeroMesh(rig, isOpaqueFrame(source)),
        talk: cached?.talk ?? buildChoreography('talk'),
        action: cached?.action ?? buildChoreography('action', 'right'),
      };
      resources.set(id, built);
      return built;
    };

    const resize = (): { width: number; height: number; dpr: number } => {
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      const width = Math.max(1, canvas.clientWidth);
      const height = Math.max(1, canvas.clientHeight);
      const pw = Math.round(width * dpr);
      const ph = Math.round(height * dpr);
      if (canvas.width !== pw || canvas.height !== ph) {
        canvas.width = pw;
        canvas.height = ph;
      }
      return { width, height, dpr };
    };

    const drawHero = (
      live: LiveProps,
      res: HeroResources,
      width: number,
      height: number,
      dpr: number,
      t: number,
      dt: number,
      camera: BackdropCamera,
    ) => {
      const talking = live.sceneMode === 'FOREGROUND_TALKING';
      const target = talking ? 0 : 1;
      modeBlend += (target - modeBlend) * (1 - Math.exp(-dt / 0.28));
      if (Math.abs(target - modeBlend) < 1e-3) modeBlend = target;
      const k = smoothstep(modeBlend);
      energy += ((live.isStreaming ? 1 : 0.35) - energy) * (1 - Math.exp(-dt / 0.35));

      // Tư thế: idle trò chuyện ⊕ hành động chỉ huy, trộn mượt khi đổi chế độ.
      const talkPose = samplePoses(res.talk, t);
      const calmTalk: RigPose = { ...talkPose };
      for (const field of POSE_FIELDS) {
        if (field !== 'breath' && field !== 'mouthWidth' && field !== 'jawOpen') calmTalk[field] = talkPose[field] * energy;
      }
      const pose = lerpPose(calmTalk, samplePoses(res.action, t), k);

      // Khẩu hình: chỉ khi FOREGROUND_TALKING + mouthSyncEnabled + đang stream.
      const mouth = mouthRef.current;
      if ((!live.isStreaming || !live.mouthSyncEnabled || !talking) && mouth.active) mouth.stop();
      if (live.mouthSyncEnabled && talking && live.isStreaming) {
        const shape = mouth.sample(performance.now());
        pose.jawOpen = shape.open * (1 - k);
        pose.mouthWidth = lerp(1, shape.width, 1 - k);
      } else {
        pose.jawOpen = 0;
        pose.mouthWidth = 1;
      }

      // Bố cục (tween khi đổi chế độ / đổi anh hùng).
      const talkLayout = computeLayout(res.rig, 'talk', width, height);
      const actionLayout = computeLayout(res.rig, 'action', width, height);
      const targetLayout: StageLayout = {
        x0: lerp(talkLayout.x0, actionLayout.x0, k),
        y0: lerp(talkLayout.y0, actionLayout.y0, k),
        scale: lerp(talkLayout.scale, actionLayout.scale, k),
      };
      if (!layout || layoutHero !== res.heroId) {
        layout = { ...targetLayout };
        layoutHero = res.heroId;
      } else {
        const r = 1 - Math.exp(-dt / 0.12);
        layout.x0 += (targetLayout.x0 - layout.x0) * r;
        layout.y0 += (targetLayout.y0 - layout.y0) * r;
        layout.scale += (targetLayout.scale - layout.scale) * r;
      }
      const depth = lerp(1.25, 0.9, k);
      const x0 = layout.x0 - camera.x * width * 0.025 * depth;
      const y0 = layout.y0 - camera.y * height * 0.02 * depth;
      const s = layout.scale;
      const rig = res.rig;

      // Hào quang phía sau + bóng đổ tiếp đất (không chạm vào điểm ảnh của model).
      const accent = hexToRgb(HEROES[res.heroId].accentColor);
      const faceCx = x0 + ((rig.face_box[0] + rig.face_box[2]) / 2) * s;
      const chestCy = y0 + rig.joints.neck[1] * s + (rig.face_box[3] - rig.face_box[1]) * s;
      const radius = rig.width * s * 0.55;
      const halo = ctx.createRadialGradient(faceCx, chestCy, 0, faceCx, chestCy, radius);
      const haloAlpha = (live.isStreaming ? 0.22 : 0.12) * (1 - 0.5 * k);
      halo.addColorStop(0, `rgba(${accent},${haloAlpha})`);
      halo.addColorStop(1, `rgba(${accent},0)`);
      ctx.fillStyle = halo;
      ctx.fillRect(faceCx - radius, chestCy - radius, radius * 2, radius * 2);
      if (k > 0.01) {
        const footY = y0 + rig.height * s;
        const shadow = ctx.createRadialGradient(faceCx, footY, 0, faceCx, footY, rig.width * s * 0.5);
        shadow.addColorStop(0, `rgba(0,0,0,${0.55 * k})`);
        shadow.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.save();
        ctx.translate(faceCx, footY);
        ctx.scale(1, 0.18);
        ctx.translate(-faceCx, -footY);
        ctx.fillStyle = shadow;
        ctx.fillRect(faceCx - rig.width * s * 0.5, footY - rig.width * s * 0.5, rig.width * s, rig.width * s);
        ctx.restore();
      }

      const pad = 0.12 * Math.max(rig.width, rig.height);
      let drawn = false;
      if (renderer.available) {
        renderer.setTexture(res.source);
        renderer.setMesh(res.mesh);
        const positions = res.mesh.deform(pose);
        const glCanvas = renderer.render(positions, pad, s * dpr);
        if (glCanvas) {
          ctx.drawImage(glCanvas, x0 - pad * s, y0 - pad * s, (rig.width + 2 * pad) * s, (rig.height + 2 * pad) * s);
          drawn = true;
        }
      }
      if (!drawn) {
        // Dự phòng không có WebGL: ảnh gốc nguyên vẹn, chỉ chuyển động toàn thân (nghiêng/thở).
        const pelvis = rig.joints.pelvis;
        ctx.save();
        ctx.translate(x0 + (pelvis[0] + pose.bodyDx * 10) * s, y0 + (pelvis[1] + pose.bodyDy * 10) * s);
        ctx.rotate((-pose.lean * Math.PI) / 180);
        ctx.scale(1 + pose.breath, 1 + pose.breath);
        ctx.drawImage(res.source, -pelvis[0] * s, -pelvis[1] * s, rig.width * s, rig.height * s);
        ctx.restore();
      }

      // Khoang miệng tối giữa hai môi khi hàm hạ (chỉ ở chế độ trò chuyện).
      if (drawn && pose.jawOpen > 0.02) {
        const cavity = res.mesh.mouthCavity(pose);
        if (cavity) {
          const cx = x0 + cavity.cx * s;
          const top = y0 + cavity.top * s;
          const bottom = y0 + cavity.bottom * s;
          const rx = Math.max(1, cavity.halfW * s);
          const ry = Math.max(0.6, (bottom - top) / 2);
          const cy = (top + bottom) / 2;
          const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rx, ry));
          g.addColorStop(0, 'rgba(26,6,6,0.92)');
          g.addColorStop(0.7, 'rgba(46,12,10,0.75)');
          g.addColorStop(1, 'rgba(60,20,16,0)');
          ctx.save();
          ctx.translate(cx, cy);
          ctx.scale(1, ry / Math.max(rx, ry));
          ctx.scale(rx / Math.max(rx, ry), 1);
          ctx.translate(-cx, -cy);
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(cx, cy, Math.max(rx, ry), 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      }
    };

    const frame = (now: number) => {
      rafId = window.requestAnimationFrame(frame);
      const live = liveRef.current;
      const dt = Math.min(0.1, Math.max(0, (now - lastNow) / 1000));
      lastNow = now;
      const t = (now - epoch) / 1000;
      const { width, height, dpr } = resize();
      const scene = live.sceneData;
      // Có backend: ảnh nền do backend chọn (thư viện → Wikimedia → phông trung tính), không vẽ cảnh thủ tục.
      // Chế độ offline (không có backdrop): cảnh động vẽ thủ tục như cũ.
      const info = scene?.backdrop;
      const photoUrl = info && info.source !== 'neutral' ? info.imageUrl : undefined;
      const theme: BackdropTheme = info
        ? photoUrl
          ? 'photo'
          : 'neutral'
        : resolveBackdropTheme({
            contextKey: scene?.contextKey ?? 'homeland',
            backgroundType: scene?.backgroundType ?? 'HOMELAND',
            sceneMode: live.sceneMode,
          });
      const key = info
        ? `${live.characterId}|${theme}|${info.contextTopicKey}|${photoUrl ?? info.title}`
        : `${live.characterId}|${theme}|${scene?.contextKey ?? 'homeland'}`;
      if (key !== backdropKey) {
        if (backdrop && transitionCtx) {
          transition.width = canvas.width;
          transition.height = canvas.height;
          transitionCtx.drawImage(canvas, 0, 0);
          transitionStart = t;
        }
        backdrop?.dispose();
        backdrop = new SceneBackdrop(
          theme,
          live.characterId,
          info?.contextTopicKey ?? scene?.contextKey ?? 'homeland',
          info ? { title: info.title, subtitle: info.subtitle } : null
        );
        backdropKey = key;
      }
      const externalUrl = info ? photoUrl : scene?.animationWebpUrl || scene?.backgroundPngUrl;
      backdrop?.setExternalImage(externalUrl ? loadImage(externalImages, externalUrl) : null);

      const k = smoothstep(modeBlend);
      const camAction = sampleCamera(cameraAction, t);
      const camVec = sampleCamera(cameraTalk, t).map((v, i) => lerp(v, camAction[i], k));
      const shake = k * 0.04;
      const camera: BackdropCamera = {
        x: camVec[0] + shake * Math.sin(t * 13.1),
        y: camVec[1] + shake * Math.sin(t * 17.3 + 1),
        zoom: camVec[2],
      };

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = '#0B0C0E';
      ctx.fillRect(0, 0, width, height);
      if (backdrop) {
        backdrop.resize(width, height);
        backdrop.draw(ctx, t, camera);
      }
      const res = getResources(live);
      if (res) drawHero(live, res, width, height, dpr, t, dt, camera);

      const fade = (t - transitionStart) / TRANSITION_S;
      if (fade < 1) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = 1 - smoothstep(fade);
        ctx.drawImage(transition, 0, 0, canvas.width, canvas.height);
        ctx.globalAlpha = 1;
      }
    };

    rafId = window.requestAnimationFrame(frame);
    return () => {
      window.cancelAnimationFrame(rafId);
      backdrop?.dispose();
      renderer.dispose();
      resources.clear();
    };
  }, []);

  return (
    <div
      id="dynamic-hero-stage"
      data-testid="dynamic-hero-stage"
      data-character-id={characterId}
      data-hero-image-url={heroImageUrl}
      data-scene-mode={sceneMode}
      data-mouth-sync={String(mouthSyncEnabled)}
      data-is-streaming={String(isStreaming)}
      data-state-id={sceneData?.stateId ?? 'default'}
      className="fixed inset-0 z-0 overflow-hidden bg-[#0B0C0E] select-none"
    >
      {/* Lớp 1–3: Cảnh nền động + model anh hùng gốc (WebGL LBS) ở 60 FPS */}
      <canvas
        ref={canvasRef}
        data-testid="stage-hero-canvas"
        role="img"
        aria-label={`${hero.fullName} — ${sceneData?.contextTitle || hero.homeland}`}
        className="absolute inset-0 h-full w-full"
      />

      {/* Lớp 4: Vignette phủ viền để làm nổi bật khung chat hơi mờ phía trước */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'linear-gradient(to right, rgba(11,12,14,0.55) 0%, rgba(11,12,14,0.08) 28%, rgba(11,12,14,0.3) 65%, rgba(11,12,14,0.72) 100%), linear-gradient(to top, rgba(11,12,14,0.7) 0%, transparent 24%, transparent 84%, rgba(11,12,14,0.6) 100%)',
        }}
      />

      {/* Ghi nguồn ảnh nền (thư viện / Wikimedia Commons) — hiển thị dạng chữ thuần, không render HTML */}
      {sceneData?.backdrop && sceneData.backdrop.source !== 'neutral' && sceneData.backdrop.imageUrl && (
        <div
          data-testid="backdrop-attribution"
          className="absolute bottom-16 right-4 z-20 max-w-[min(420px,calc(100vw-32px))] truncate rounded-md bg-[#0B0C0E]/70 px-2.5 py-1 text-[10px] text-zinc-300 backdrop-blur-sm"
        >
          {'Ảnh: '}
          {sceneData.backdrop.author || 'không rõ tác giả'}
          {sceneData.backdrop.license ? ' · ' : ''}
          {safeHttpUrl(sceneData.backdrop.licenseUrl) ? (
            <a
              href={safeHttpUrl(sceneData.backdrop.licenseUrl)}
              target="_blank"
              rel="noopener noreferrer"
              className="underline decoration-zinc-500 hover:text-white"
            >
              {sceneData.backdrop.license}
            </a>
          ) : (
            sceneData.backdrop.license
          )}
          {safeHttpUrl(sceneData.backdrop.sourceUrl) && (
            <>
              {' · '}
              <a
                href={safeHttpUrl(sceneData.backdrop.sourceUrl)}
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-zinc-500 hover:text-white"
              >
                {sceneData.backdrop.source === 'wikimedia' ? 'Wikimedia Commons' : 'Nguồn ảnh'}
              </a>
            </>
          )}
        </div>
      )}

      {/* Chỉ báo trạng thái đồng bộ khẩu hình / hành động */}
      <div className="pointer-events-none absolute bottom-6 left-1/2 z-20 -translate-x-1/2 lg:left-[calc(290px+17vw)]">
        <div className="flex items-center gap-2 rounded-full border border-white/[0.1] bg-[#0B0C0E]/80 px-3.5 py-1.5 text-[11px] text-zinc-300 backdrop-blur-md shadow-lg whitespace-nowrap">
          {mouthSyncEnabled ? (
            <>
              <span className={`h-2 w-2 rounded-full ${isStreaming ? 'bg-emerald-400 animate-ping' : 'bg-emerald-400'}`} />
              <span className="font-medium text-zinc-200">Tiền cảnh trò chuyện</span>
              <span className="text-zinc-500">·</span>
              <span className="text-emerald-300/90">{isStreaming ? 'Đang khớp khẩu hình' : 'Khẩu hình sẵn sàng'}</span>
            </>
          ) : (
            <>
              <Swords className="h-3.5 w-3.5 text-amber-400" />
              <span className="font-medium text-amber-200">Tái hiện sự kiện / trận đánh</span>
              <span className="text-zinc-500">·</span>
              <span className="text-zinc-400">Tắt khẩu hình</span>
            </>
          )}
        </div>
      </div>

      {/* Lớp 5: Bảng thông tin bối cảnh sân khấu ( góc trên bên phải ) */}
      <div className="pointer-events-none absolute right-5 top-16 z-10 hidden xl:flex flex-col items-end gap-2">
        <div className="flex items-center gap-2 rounded-2xl border border-white/[0.08] bg-[#12141A]/65 px-3.5 py-2 text-xs text-zinc-300 backdrop-blur-md">
          {isActionMode ? <Compass className="h-3.5 w-3.5 text-amber-400" /> : <Shield className="h-3.5 w-3.5 text-[#D4B982]" />}
          <div className="flex flex-col text-right">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">Bối cảnh sân khấu ({sceneMode})</span>
            <span className="font-medium text-zinc-200">
              {sceneData?.contextTitle || hero.homeland}
              {sceneData?.contextYear ? ` · ${sceneData.contextYear}` : ''}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-[#12141A]/50 px-3 py-1 text-[11px] text-zinc-400 backdrop-blur-md">
          <Eye className="h-3 w-3 text-zinc-400" />
          <span>
            Rig 2.5D:{' '}
            {sceneData?.rigData ? `${Object.keys(sceneData.rigData.joints).length} khớp xương` : 'Đã gắn khung chuẩn'}
          </span>
          <span className="text-zinc-600">|</span>
          <Sparkles className="h-3 w-3 text-[#D4B982]" />
          <span>MouthSync: {mouthSyncEnabled ? 'ON' : 'OFF'}</span>
        </div>
      </div>
    </div>
  );
};
