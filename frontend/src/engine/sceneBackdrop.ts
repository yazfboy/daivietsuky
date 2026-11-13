/**
 * Cảnh nền động 60 FPS vẽ thủ tục trên Canvas 2D (không tải thêm tài nguyên mạng):
 *  - homeland : quê hương từng vị anh hùng (Vạn Kiếp – lúa, sông, tre, cò; Côn Sơn – thông, suối, lá rơi;
 *               Tây Sơn – cây me, đồng lúa chín, bụi nắng) — mây trôi, chim bay, cây cỏ đung đưa, sương lướt.
 *  - naval    : trận Bạch Đằng — sóng nhiều lớp phối cảnh, bãi cọc gỗ lộ dần khi thủy triều rút, chiến thuyền
 *               nhấp nhô & bốc cháy, khói, tàn lửa, mưa tên lửa, thuyền nhẹ Đại Việt, cờ bay.
 *  - land     : trận trên bộ — đoàn quân hành tiến, cờ, đồn lũy cháy, khói, bụi, mưa tên.
 *  - hall     : sự kiện (Hịch tướng sĩ, Bình Ngô đại cáo) — điện gỗ son, đèn lồng đung đưa, khói trầm, tia nắng.
 * Lớp tĩnh (trời, núi xa) dựng sẵn vào canvas phụ khi đổi kích thước; mọi thứ chuyển động vẽ lại từng khung.
 */
import type { BackgroundType, HeroId, SceneMode } from '../types/hero';

const TAU = Math.PI * 2;

/**
 * 'photo'   : ảnh nền do backend chọn (thư viện dựng sẵn / Wikimedia) — không vẽ thêm lớp thủ tục nào lên ảnh.
 * 'neutral' : phông trung tính (nền giấy dó, nét bản đồ mờ + tên địa danh) khi không có ảnh đạt chuẩn.
 */
export type BackdropTheme = 'homeland' | 'naval' | 'land' | 'hall' | 'photo' | 'neutral';

export interface BackdropLabel {
  title: string;
  subtitle?: string;
}

export interface BackdropCamera {
  /** −1..1, độ lệch ngang của máy quay */
  x: number;
  /** −1..1, độ lệch dọc */
  y: number;
  /** ≥ 1, độ phóng */
  zoom: number;
}

type Rng = () => number;

function mulberry32(seed: number): Rng {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashString(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

const wrap = (v: number, lo: number, hi: number) => {
  const span = hi - lo;
  return lo + ((((v - lo) % span) + span) % span);
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

function makeCanvas(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  return canvas;
}

function radialSprite(rgb: string, size = 64, core = 0): HTMLCanvasElement {
  const canvas = makeCanvas(size, size);
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, `rgba(${rgb},1)`);
    g.addColorStop(Math.max(0.01, core), `rgba(${rgb},0.75)`);
    g.addColorStop(1, `rgba(${rgb},0)`);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
  }
  return canvas;
}

/** Đường sống núi: bao trên của các "chuông" Gauss → đỉnh núi đá vôi dựng đứng hoặc đồi thoải. */
function makeRidge(rng: Rng, peaks: number, sharp: number): (u: number) => number {
  const list = Array.from({ length: peaks }, () => ({
    c: rng() * 1.2 - 0.1,
    w: (0.03 + rng() * 0.08) / sharp,
    h: 0.35 + rng() * 0.65,
  }));
  return (u: number) => {
    let best = 0.08 + 0.04 * Math.sin(u * 17.3);
    for (const p of list) best = Math.max(best, p.h * Math.exp(-(((u - p.c) / p.w) ** 2)));
    return best;
  };
}

function fillRidge(
  ctx: CanvasRenderingContext2D,
  w: number,
  baseY: number,
  height: number,
  ridge: (u: number) => number,
  fill: string | CanvasGradient,
  bottom: number,
): void {
  ctx.beginPath();
  ctx.moveTo(-4, bottom);
  for (let x = -4; x <= w + 4; x += 4) ctx.lineTo(x, baseY - ridge(x / w) * height);
  ctx.lineTo(w + 4, bottom);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  grow: number;
  life: number;
  maxLife: number;
  seed: number;
}

class ParticlePool {
  readonly items: Particle[] = [];
  constructor(private readonly capacity: number) {}

  spawn(p: Particle): void {
    if (this.items.length >= this.capacity) this.items.shift();
    this.items.push(p);
  }

  update(dt: number, drift: (p: Particle) => void): void {
    for (let i = this.items.length - 1; i >= 0; i -= 1) {
      const p = this.items[i];
      p.life += dt;
      if (p.life >= p.maxLife) {
        this.items.splice(i, 1);
        continue;
      }
      drift(p);
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.size += p.grow * dt;
    }
  }

  clear(): void {
    this.items.length = 0;
  }
}

interface HomelandStyle {
  sky: [string, string, string];
  sun: string;
  far: string;
  near: string;
  ground: [string, string];
  water: [string, string];
  foliage: string;
  blade: [string, string, string];
  riverWidth: number;
  birdColor: string;
  mote: string;
  tree: 'bamboo' | 'pine' | 'tamarind';
}

const HOMELAND_STYLES: Record<HeroId, HomelandStyle> = {
  // Vạn Kiếp – Kiếp Bạc (Hải Dương): bình minh vàng, sông Lục Đầu, lúa xanh, lũy tre, cò trắng.
  tran_hung_dao: {
    sky: ['#1b2440', '#b9785a', '#f0c27b'],
    sun: '255,214,150',
    far: '#4b4d66',
    near: '#2d3836',
    ground: ['#3f5a2c', '#1d2a16'],
    water: ['#8a8f99', '#3d4a55'],
    foliage: '#24361e',
    blade: ['#4f7a34', '#6b9440', '#3a5c27'],
    riverWidth: 0.07,
    birdColor: 'rgba(245,245,240,0.92)',
    mote: '255,240,200',
    tree: 'bamboo',
  },
  // Côn Sơn (Chí Linh): sớm sương xanh, rừng thông, suối nhỏ, lá thông rơi.
  nguyen_trai: {
    sky: ['#0f2230', '#47706b', '#b4cba9'],
    sun: '232,240,208',
    far: '#3a5560',
    near: '#1f3a35',
    ground: ['#2b4632', '#122019'],
    water: ['#87aaa4', '#365c5a'],
    foliage: '#14281d',
    blade: ['#3a6040', '#4f7a50', '#2a4a30'],
    riverWidth: 0.035,
    birdColor: 'rgba(20,30,28,0.85)',
    mote: '210,230,190',
    tree: 'pine',
  },
  // Tây Sơn (Bình Định): chiều vàng, đồng lúa chín, cây me cổ thụ, bụi nắng.
  quang_trung: {
    sky: ['#2a1d3a', '#b8643c', '#f2b56b'],
    sun: '255,207,122',
    far: '#5c4a5a',
    near: '#3b3330',
    ground: ['#7a6a32', '#3a2f17'],
    water: ['#b09070', '#5a4a3a'],
    foliage: '#2e3a1c',
    blade: ['#a08a3a', '#c2a84a', '#7d6a2a'],
    riverWidth: 0.03,
    birdColor: 'rgba(30,22,20,0.85)',
    mote: '255,220,160',
    tree: 'tamarind',
  },
};

const NAVAL_KEY = /bach_dang|van_don|rach_gam|xoai_mut|chuong_duong|ham_tu|thuy_chien|naval/;

export function resolveBackdropTheme(scene: {
  contextKey: string;
  backgroundType: BackgroundType;
  sceneMode: SceneMode;
}): BackdropTheme {
  const key = (scene.contextKey || '').toLowerCase();
  if (NAVAL_KEY.test(key)) return 'naval';
  if (scene.backgroundType === 'HISTORICAL_EVENT') return 'hall';
  if (scene.backgroundType === 'BATTLEFIELD') return 'land';
  // Cảnh hành động không rõ bối cảnh → mặc định tái hiện trận Bạch Đằng.
  return scene.sceneMode === 'BACKGROUND_ACTION' ? 'naval' : 'homeland';
}

interface Blade {
  x: number;
  y: number;
  len: number;
  phase: number;
  shade: number;
}
interface Bird {
  x: number;
  y: number;
  speed: number;
  size: number;
  phase: number;
}
interface Stake {
  x: number;
  z: number;
  tilt: number;
  height: number;
}
interface Junk {
  x: number;
  z: number;
  phase: number;
  burning: boolean;
  wreck: number;
}
interface Skiff {
  x: number;
  z: number;
  speed: number;
  phase: number;
}
interface Arrow {
  sx: number;
  sy: number;
  tx: number;
  ty: number;
  period: number;
  offset: number;
  flaming: boolean;
  arc: number;
}
interface Soldier {
  x: number;
  rank: number;
  phase: number;
  banner: boolean;
  shield: boolean;
}
interface Lantern {
  x: number;
  length: number;
  size: number;
  phase: number;
}
interface FirePoint {
  x: number;
  y: number;
  size: number;
  seed: number;
}

export class SceneBackdrop {
  readonly theme: BackdropTheme;
  private readonly heroId: HeroId;
  private readonly style: HomelandStyle;
  private readonly seed: number;
  private width = 0;
  private height = 0;
  private staticLayer: HTMLCanvasElement | null = null;
  private external: HTMLImageElement | null = null;
  private lastTime = -1;

  private readonly sprites = {
    smoke: radialSprite('46,40,38', 64, 0.2),
    smokeWarm: radialSprite('150,88,52', 64, 0.15),
    smokeLight: radialSprite('222,216,206', 64, 0.1),
    glow: radialSprite('255,160,70', 64, 0.05),
    glowSoft: radialSprite('255,228,175', 64, 0.05),
    cloud: radialSprite('255,250,245', 64, 0.35),
  };

  private readonly smoke = new ParticlePool(170);
  private readonly embers = new ParticlePool(140);
  private readonly motes = new ParticlePool(70);
  private emitAccumulator = 0;

  private blades: Blade[] = [];
  private reeds: Blade[] = [];
  private birds: Bird[] = [];
  private clouds: Array<{ x: number; y: number; w: number; speed: number }> = [];
  private stakes: Stake[] = [];
  private junks: Junk[] = [];
  private skiffs: Skiff[] = [];
  private arrows: Arrow[] = [];
  private soldiers: Soldier[] = [];
  private lanterns: Lantern[] = [];
  private trees: Array<{ x: number; scale: number; phase: number }> = [];
  private farFires: FirePoint[] = [];

  private readonly label: BackdropLabel | null;

  constructor(theme: BackdropTheme, heroId: HeroId, contextKey: string, label: BackdropLabel | null = null) {
    this.theme = theme;
    this.heroId = heroId;
    this.label = label;
    this.style = HOMELAND_STYLES[heroId];
    this.seed = hashString(`${theme}:${heroId}:${contextKey}`);
    this.populate();
  }

  /** Ảnh/WebP bối cảnh do Phase 2 sinh (nếu có) làm lớp nền thay cho bầu trời vẽ thủ tục. */
  setExternalImage(image: HTMLImageElement | null): void {
    this.external = image;
  }

  private populate(): void {
    const rng = mulberry32(this.seed);
    const blades = (count: number, y0: number, y1: number, len0: number, len1: number, x0 = 0, x1 = 1): Blade[] =>
      Array.from({ length: count }, () => ({
        x: lerp(x0, x1, rng()),
        y: lerp(y0, y1, rng()),
        len: lerp(len0, len1, rng()),
        phase: rng() * TAU,
        shade: Math.floor(rng() * 3),
      })).sort((a, b) => a.y - b.y);
    this.clouds = Array.from({ length: 6 }, () => ({
      x: rng(),
      y: 0.06 + rng() * 0.28,
      w: 0.12 + rng() * 0.2,
      speed: 0.004 + rng() * 0.01,
    }));
    this.birds = Array.from({ length: 7 }, () => ({
      x: rng(),
      y: 0.12 + rng() * 0.25,
      speed: 0.015 + rng() * 0.025,
      size: 5 + rng() * 6,
      phase: rng() * TAU,
    }));
    if (this.theme === 'homeland') {
      this.blades = blades(this.heroId === 'nguyen_trai' ? 160 : 340, 0.8, 1.02, 0.03, 0.08);
      const treeCount = this.style.tree === 'pine' ? 6 : this.style.tree === 'bamboo' ? 7 : 1;
      this.trees = Array.from({ length: treeCount }, (_, i) => ({
        x:
          this.style.tree === 'pine'
            ? [0.04, 0.12, 0.82, 0.9, 0.97, 0.68][i]
            : this.style.tree === 'bamboo'
              ? 0.84 + (i - 3) * 0.018 + rng() * 0.01
              : 0.84,
        scale: 0.75 + rng() * 0.45,
        phase: rng() * TAU,
      }));
    }
    if (this.theme === 'naval') {
      this.stakes = Array.from({ length: 22 }, (_, i) => ({
        x: 0.36 + (i % 11) * 0.06 + rng() * 0.03,
        z: i < 11 ? 0.3 + rng() * 0.08 : 0.44 + rng() * 0.1,
        tilt: -0.42 + rng() * 0.22,
        height: 0.8 + rng() * 0.5,
      }));
      this.junks = [
        { x: 0.58, z: 0.2, phase: rng() * TAU, burning: true, wreck: 0.12 },
        { x: 0.84, z: 0.16, phase: rng() * TAU, burning: true, wreck: 0 },
        { x: 0.7, z: 0.34, phase: rng() * TAU, burning: true, wreck: 0.22 },
        { x: 0.97, z: 0.28, phase: rng() * TAU, burning: false, wreck: 0 },
        { x: 0.45, z: 0.13, phase: rng() * TAU, burning: false, wreck: 0 },
      ];
      this.skiffs = Array.from({ length: 3 }, (_, i) => ({
        x: rng(),
        z: 0.55 + i * 0.1 + rng() * 0.04,
        speed: 0.018 + rng() * 0.012,
        phase: rng() * TAU,
      }));
      this.reeds = [...blades(70, 0.9, 1.03, 0.08, 0.2, -0.02, 0.2), ...blades(40, 0.92, 1.03, 0.06, 0.16, 0.86, 1.02)];
      this.farFires = [0.55, 0.74, 0.9].map((x) => ({ x, y: 0, size: 0.6 + rng() * 0.4, seed: rng() * 100 }));
    }
    if (this.theme === 'naval' || this.theme === 'land') {
      this.arrows = Array.from({ length: 12 }, () => ({
        sx: rng() * 0.22,
        sy: 0.78 + rng() * 0.18,
        tx: 0.45 + rng() * 0.5,
        ty: this.theme === 'naval' ? 0.58 + rng() * 0.1 : 0.6 + rng() * 0.12,
        period: 2.6 + rng() * 2.2,
        offset: rng() * 6,
        flaming: rng() < 0.4,
        arc: 0.16 + rng() * 0.14,
      }));
    }
    if (this.theme === 'land') {
      this.soldiers = Array.from({ length: 54 }, (_, i) => ({
        x: rng(),
        rank: i < 30 ? 0 : 1,
        phase: rng() * TAU,
        banner: i % 9 === 0,
        shield: rng() < 0.35,
      }));
      this.blades = blades(150, 0.9, 1.03, 0.03, 0.07);
      this.farFires = [0.42, 0.63, 0.86].map((x) => ({ x, y: 0, size: 0.6 + rng() * 0.5, seed: rng() * 100 }));
    }
    if (this.theme === 'hall') {
      this.lanterns = [0.14, 0.36, 0.64, 0.86].map((x, i) => ({
        x,
        length: 0.12 + (i % 2) * 0.05,
        size: 0.9 + rng() * 0.2,
        phase: rng() * TAU,
      }));
    }
  }

  private horizon(): number {
    switch (this.theme) {
      case 'naval':
        return 0.56 * this.height;
      case 'land':
        return 0.64 * this.height;
      case 'hall':
        return 0.72 * this.height;
      default:
        return 0.66 * this.height;
    }
  }

  /** Dựng lớp tĩnh (trời, núi xa, nền) — chỉ gọi khi đổi kích thước. */
  resize(width: number, height: number): void {
    if (width === this.width && height === this.height && this.staticLayer) return;
    this.width = width;
    this.height = height;
    const layer = makeCanvas(width, height);
    const ctx = layer.getContext('2d');
    if (!ctx) return;
    const rng = mulberry32(this.seed ^ 0x9e3779b9);
    const w = width;
    const h = height;
    const H0 = this.horizon();
    if (this.theme === 'neutral' || this.theme === 'photo') {
      this.paintPaper(ctx, w, h, rng, this.theme === 'neutral');
    } else if (this.theme === 'homeland') {
      const s = this.style;
      const sky = ctx.createLinearGradient(0, 0, 0, H0);
      sky.addColorStop(0, s.sky[0]);
      sky.addColorStop(0.6, s.sky[1]);
      sky.addColorStop(1, s.sky[2]);
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, w, h);
      const sun = ctx.createRadialGradient(0.62 * w, 0.42 * H0, 0, 0.62 * w, 0.42 * H0, 0.45 * w);
      sun.addColorStop(0, `rgba(${s.sun},0.9)`);
      sun.addColorStop(0.08, `rgba(${s.sun},0.55)`);
      sun.addColorStop(1, `rgba(${s.sun},0)`);
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, w, h);
      fillRidge(ctx, w, H0 - 0.02 * h, 0.3 * h, makeRidge(rng, 9, this.heroId === 'nguyen_trai' ? 1.4 : 0.8), s.far, h);
      fillRidge(ctx, w, H0 + 0.01 * h, 0.16 * h, makeRidge(rng, 12, 0.7), s.near, h);
      const ground = ctx.createLinearGradient(0, H0, 0, h);
      ground.addColorStop(0, s.ground[0]);
      ground.addColorStop(1, s.ground[1]);
      ctx.fillStyle = ground;
      ctx.fillRect(0, H0 + 0.01 * h, w, h);
      // Ruộng bậc: các bờ ruộng mảnh song song tạo phối cảnh.
      ctx.strokeStyle = 'rgba(0,0,0,0.16)';
      ctx.lineWidth = 1;
      for (let i = 1; i < 9; i += 1) {
        const y = H0 + 0.01 * h + (h - H0) * (i / 9) ** 1.6;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.bezierCurveTo(0.3 * w, y - 6, 0.7 * w, y + 6, w, y);
        ctx.stroke();
      }
    } else if (this.theme === 'naval') {
      const sky = ctx.createLinearGradient(0, 0, 0, H0);
      sky.addColorStop(0, '#120c12');
      sky.addColorStop(0.45, '#3f1c1a');
      sky.addColorStop(0.85, '#a8492a');
      sky.addColorStop(1, '#d9773a');
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, w, h);
      fillRidge(ctx, w, H0, 0.34 * h, makeRidge(rng, 14, 2.4), '#3a1f22', h);
      fillRidge(ctx, w, H0, 0.2 * h, makeRidge(rng, 10, 2.8), '#24141a', h);
      const water = ctx.createLinearGradient(0, H0, 0, h);
      water.addColorStop(0, '#5b2c22');
      water.addColorStop(0.25, '#2a1a1c');
      water.addColorStop(1, '#0a0e12');
      ctx.fillStyle = water;
      ctx.fillRect(0, H0, w, h - H0);
      // Bờ trái (phía quân Đại Việt mai phục).
      fillRidge(ctx, 0.3 * w, H0 + 0.05 * h, 0.12 * h, makeRidge(rng, 5, 0.9), '#140d0e', h);
    } else if (this.theme === 'land') {
      const sky = ctx.createLinearGradient(0, 0, 0, H0);
      sky.addColorStop(0, '#16121a');
      sky.addColorStop(0.5, '#5a3026');
      sky.addColorStop(1, '#c46a3a');
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, w, h);
      fillRidge(ctx, w, H0 - 0.02 * h, 0.38 * h, makeRidge(rng, 12, 2), '#3c2626', h);
      fillRidge(ctx, w, H0 + 0.01 * h, 0.22 * h, makeRidge(rng, 9, 1.2), '#271a1a', h);
      const ground = ctx.createLinearGradient(0, H0, 0, h);
      ground.addColorStop(0, '#3a2a1e');
      ground.addColorStop(1, '#100c0a');
      ctx.fillStyle = ground;
      ctx.fillRect(0, H0 + 0.01 * h, w, h);
    } else {
      const wall = ctx.createLinearGradient(0, 0, 0, H0);
      wall.addColorStop(0, '#120a08');
      wall.addColorStop(1, '#3a1810');
      ctx.fillStyle = wall;
      ctx.fillRect(0, 0, w, h);
      // Cửa song (lattice) hai bên.
      for (const cx of [0.25, 0.75]) {
        const x0 = cx * w - 0.08 * w;
        const y0 = 0.2 * h;
        const ww = 0.16 * w;
        const hh = 0.32 * h;
        const light = ctx.createLinearGradient(0, y0, 0, y0 + hh);
        light.addColorStop(0, 'rgba(255,196,120,0.55)');
        light.addColorStop(1, 'rgba(160,80,40,0.25)');
        ctx.fillStyle = light;
        ctx.fillRect(x0, y0, ww, hh);
        ctx.strokeStyle = '#1e0d08';
        ctx.lineWidth = 3;
        for (let i = 0; i <= 6; i += 1) {
          ctx.beginPath();
          ctx.moveTo(x0 + (ww * i) / 6, y0);
          ctx.lineTo(x0 + (ww * i) / 6, y0 + hh);
          ctx.stroke();
        }
        for (let i = 0; i <= 8; i += 1) {
          ctx.beginPath();
          ctx.moveTo(x0, y0 + (hh * i) / 8);
          ctx.lineTo(x0 + ww, y0 + (hh * i) / 8);
          ctx.stroke();
        }
      }
      // Hoành phi sơn son thếp vàng.
      ctx.fillStyle = '#5a1a10';
      ctx.fillRect(0.36 * w, 0.06 * h, 0.28 * w, 0.08 * h);
      ctx.strokeStyle = '#c9a24a';
      ctx.lineWidth = 3;
      ctx.strokeRect(0.36 * w + 4, 0.06 * h + 4, 0.28 * w - 8, 0.08 * h - 8);
      // Cột son với đấu vàng.
      for (const px of [0.06, 0.4, 0.6, 0.94]) {
        const cw = (px === 0.06 || px === 0.94 ? 0.06 : 0.035) * w;
        const g = ctx.createLinearGradient(px * w - cw / 2, 0, px * w + cw / 2, 0);
        g.addColorStop(0, '#3a0c08');
        g.addColorStop(0.45, '#8c2316');
        g.addColorStop(1, '#2a0806');
        ctx.fillStyle = g;
        ctx.fillRect(px * w - cw / 2, 0, cw, H0 + 0.02 * h);
        ctx.fillStyle = '#b48a3c';
        ctx.fillRect(px * w - cw * 0.65, 0.03 * h, cw * 1.3, 0.025 * h);
        ctx.fillRect(px * w - cw * 0.6, H0 - 0.01 * h, cw * 1.2, 0.02 * h);
      }
      const floor = ctx.createLinearGradient(0, H0, 0, h);
      floor.addColorStop(0, '#3a2014');
      floor.addColorStop(1, '#120a06');
      ctx.fillStyle = floor;
      ctx.fillRect(0, H0, w, h - H0);
      ctx.strokeStyle = 'rgba(0,0,0,0.25)';
      ctx.lineWidth = 1;
      for (let i = -10; i <= 10; i += 1) {
        ctx.beginPath();
        ctx.moveTo(w / 2 + i * 0.02 * w, H0);
        ctx.lineTo(w / 2 + i * 0.16 * w, h);
        ctx.stroke();
      }
      // Hương án.
      ctx.fillStyle = '#2a120a';
      ctx.fillRect(0.58 * w, 0.78 * h, 0.3 * w, 0.06 * h);
      ctx.fillStyle = '#4a2010';
      ctx.fillRect(0.58 * w, 0.78 * h, 0.3 * w, 0.012 * h);
    }
    this.staticLayer = layer;
  }

  /** Vẽ một khung hình. ``t`` giây, ``camera`` từ quỹ đạo DP của sân khấu. */
  draw(ctx: CanvasRenderingContext2D, t: number, camera: BackdropCamera): void {
    const w = this.width;
    const h = this.height;
    if (!w || !h) return;
    const dt = this.lastTime < 0 ? 1 / 60 : Math.min(0.1, Math.max(0, t - this.lastTime));
    this.lastTime = t;
    ctx.save();
    // Overscan nhẹ để parallax không bao giờ lộ mép.
    ctx.translate(w / 2, h / 2);
    ctx.scale(1.06 * camera.zoom, 1.06 * camera.zoom);
    ctx.translate(-w / 2, -h / 2);
    this.layer(ctx, camera, 0.25, () => {
      if (this.external && this.external.complete && this.external.naturalWidth > 0) {
        const img = this.external;
        const k = Math.max(w / img.naturalWidth, h / img.naturalHeight);
        ctx.drawImage(img, (w - img.naturalWidth * k) / 2, (h - img.naturalHeight * k) / 2, img.naturalWidth * k, img.naturalHeight * k);
      } else if (this.staticLayer) {
        ctx.drawImage(this.staticLayer, 0, 0, w, h);
      }
    });
    switch (this.theme) {
      case 'photo':
        break;
      case 'neutral':
        this.drawNeutralLight(ctx, t);
        break;
      case 'homeland':
        this.drawHomeland(ctx, t, dt, camera);
        break;
      case 'naval':
        this.drawNaval(ctx, t, dt, camera);
        break;
      case 'land':
        this.drawLand(ctx, t, dt, camera);
        break;
      default:
        this.drawHall(ctx, t, dt, camera);
    }
    ctx.restore();
  }

  /** Nền giấy dó: sắc ngà, xơ giấy, nét bản đồ cổ mờ; ``withLabel`` thì in tên địa danh. */
  private paintPaper(ctx: CanvasRenderingContext2D, w: number, h: number, rng: Rng, withLabel: boolean): void {
    const base = ctx.createLinearGradient(0, 0, w, h);
    base.addColorStop(0, '#e9dcc0');
    base.addColorStop(0.55, '#dccaa2');
    base.addColorStop(1, '#c4ad7f');
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, w, h);
    // Xơ giấy dó.
    ctx.lineWidth = 1;
    for (let i = 0; i < Math.round((w * h) / 2600); i += 1) {
      const x = rng() * w;
      const y = rng() * h;
      const len = 4 + rng() * 16;
      const a = rng() * TAU;
      ctx.strokeStyle = `rgba(${rng() < 0.5 ? '120,96,60' : '255,250,235'},${0.06 + rng() * 0.1})`;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.quadraticCurveTo(x + Math.cos(a) * len * 0.5 + 2, y + Math.sin(a) * len * 0.5 - 2, x + Math.cos(a) * len, y + Math.sin(a) * len);
      ctx.stroke();
    }
    // Lưới kinh vĩ + đường bờ sông/biển mờ kiểu dư đồ cổ.
    ctx.strokeStyle = 'rgba(92,64,36,0.12)';
    for (let i = 1; i < 8; i += 1) {
      ctx.beginPath();
      ctx.moveTo((w * i) / 8, 0);
      ctx.lineTo((w * i) / 8, h);
      ctx.stroke();
    }
    for (let i = 1; i < 5; i += 1) {
      ctx.beginPath();
      ctx.moveTo(0, (h * i) / 5);
      ctx.lineTo(w, (h * i) / 5);
      ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(92,64,36,0.22)';
    ctx.lineWidth = 1.5;
    for (let line = 0; line < 3; line += 1) {
      const y0 = h * (0.3 + line * 0.22);
      const amp = h * (0.03 + rng() * 0.04);
      const phase = rng() * TAU;
      ctx.beginPath();
      for (let i = 0; i <= 48; i += 1) {
        const x = (w * i) / 48;
        const y = y0 + Math.sin((i / 48) * TAU * (1.2 + line * 0.4) + phase) * amp + Math.sin(i * 1.7) * amp * 0.15;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    // Viền ố vàng.
    const edge = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.35, w / 2, h / 2, Math.max(w, h) * 0.75);
    edge.addColorStop(0, 'rgba(120,84,40,0)');
    edge.addColorStop(1, 'rgba(96,62,28,0.45)');
    ctx.fillStyle = edge;
    ctx.fillRect(0, 0, w, h);
    if (withLabel && this.label?.title) {
      const size = Math.max(22, Math.min(64, w * 0.042));
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(58,38,20,0.78)';
      ctx.font = `600 ${size}px "Cormorant Garamond", "Noto Serif", Georgia, serif`;
      ctx.fillText(this.label.title, w * 0.6, h * 0.2, w * 0.7);
      if (this.label.subtitle) {
        ctx.fillStyle = 'rgba(58,38,20,0.55)';
        ctx.font = `500 ${size * 0.5}px "Cormorant Garamond", "Noto Serif", Georgia, serif`;
        ctx.fillText(this.label.subtitle, w * 0.6, h * 0.2 + size * 0.95, w * 0.6);
      }
      ctx.strokeStyle = 'rgba(58,38,20,0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(w * 0.6 - size * 3, h * 0.2 + size * (this.label.subtitle ? 1.55 : 0.8));
      ctx.lineTo(w * 0.6 + size * 3, h * 0.2 + size * (this.label.subtitle ? 1.55 : 0.8));
      ctx.stroke();
    }
  }

  /** Vệt sáng trôi chậm trên nền giấy (chuyển động nhẹ, không thêm chi tiết cảnh). */
  private drawNeutralLight(ctx: CanvasRenderingContext2D, t: number): void {
    const w = this.width;
    const h = this.height;
    const x = (0.35 + 0.25 * Math.sin(t * 0.07)) * w;
    const y = (0.4 + 0.1 * Math.cos(t * 0.05)) * h;
    ctx.save();
    ctx.globalAlpha = 0.35;
    ctx.drawImage(this.sprites.glowSoft, x - 0.4 * w, y - 0.4 * w, 0.8 * w, 0.8 * w);
    ctx.restore();
  }

  private layer(ctx: CanvasRenderingContext2D, camera: BackdropCamera, depth: number, paint: () => void): void {
    ctx.save();
    ctx.translate(-camera.x * this.width * 0.025 * depth, -camera.y * this.height * 0.02 * depth);
    paint();
    ctx.restore();
  }

  // ───────────────────────── Quê hương ─────────────────────────

  private drawHomeland(ctx: CanvasRenderingContext2D, t: number, dt: number, camera: BackdropCamera): void {
    const w = this.width;
    const h = this.height;
    const s = this.style;
    const H0 = this.horizon();
    this.layer(ctx, camera, 0.3, () => {
      // Nắng thở nhẹ.
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.12 + 0.05 * Math.sin(t * 0.6);
      ctx.drawImage(this.sprites.glowSoft, 0.62 * w - 0.25 * w, 0.42 * H0 - 0.25 * w, 0.5 * w, 0.5 * w);
      ctx.restore();
      this.drawClouds(ctx, t);
      this.drawBirds(ctx, t, s.birdColor);
    });
    this.layer(ctx, camera, 0.55, () => {
      // Dòng sông / suối lấp lánh.
      const ry = H0 + 0.05 * h;
      const rh = s.riverWidth * h;
      const water = ctx.createLinearGradient(0, ry, 0, ry + rh);
      water.addColorStop(0, s.water[0]);
      water.addColorStop(1, s.water[1]);
      ctx.fillStyle = water;
      ctx.beginPath();
      ctx.moveTo(-10, ry + Math.sin(t * 0.3) * 1.5);
      for (let x = 0; x <= w + 20; x += 20) ctx.lineTo(x, ry + 4 * Math.sin(x * 0.006 + 1.3) + Math.sin(t * 0.8 + x * 0.02));
      for (let x = w + 20; x >= -10; x -= 20) ctx.lineTo(x, ry + rh + 6 * Math.sin(x * 0.004 + 0.4));
      ctx.closePath();
      ctx.fill();
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.strokeStyle = `rgba(${s.sun},0.35)`;
      ctx.lineWidth = 1.2;
      for (let i = 0; i < 46; i += 1) {
        const x = wrap(i * 97.3 + t * (14 + (i % 5) * 3), -40, w + 40);
        const y = ry + ((i * 37) % 100) / 100 * rh * 0.8 + rh * 0.1;
        const a = 0.5 + 0.5 * Math.sin(t * 2.4 + i * 1.7);
        ctx.globalAlpha = a;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + 10 + (i % 4) * 6, y);
        ctx.stroke();
      }
      ctx.restore();
      this.drawMist(ctx, t, H0 + 0.02 * h, 0.22);
    });
    this.layer(ctx, camera, 0.8, () => {
      if (s.tree === 'pine') this.drawPines(ctx, t, s.foliage);
      else if (s.tree === 'bamboo') this.drawBamboo(ctx, t, s.foliage);
      else this.drawTamarind(ctx, t, s.foliage);
    });
    this.layer(ctx, camera, 1, () => {
      this.drawBlades(ctx, this.blades, t, s.blade, 1);
      // Bụi nắng / lá rơi / phấn hoa trôi.
      const rng = Math.random;
      this.emitAccumulator += dt * 9;
      while (this.emitAccumulator >= 1) {
        this.emitAccumulator -= 1;
        this.motes.spawn({
          x: rng() * w,
          y: rng() * h * 0.9,
          vx: (this.style.tree === 'pine' ? 14 : 5) + rng() * 8,
          vy: this.style.tree === 'pine' ? 18 + rng() * 14 : -3 + rng() * 6,
          size: 1 + rng() * 2.2,
          grow: 0,
          life: 0,
          maxLife: 5 + rng() * 4,
          seed: rng() * TAU,
        });
      }
      this.motes.update(dt, (p) => {
        p.vx += Math.sin(t * 1.3 + p.seed) * 6 * dt;
      });
      ctx.save();
      for (const p of this.motes.items) {
        const k = p.life / p.maxLife;
        const alpha = Math.sin(Math.PI * k) * 0.8;
        if (s.tree === 'pine') {
          ctx.fillStyle = `rgba(150,120,60,${alpha})`;
          ctx.beginPath();
          ctx.ellipse(p.x, p.y, p.size * 2.4, p.size * 0.7, t * 2 + p.seed, 0, TAU);
          ctx.fill();
        } else {
          ctx.globalCompositeOperation = 'lighter';
          ctx.fillStyle = `rgba(${s.mote},${alpha * 0.7})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, TAU);
          ctx.fill();
        }
      }
      ctx.restore();
    });
  }

  private drawClouds(ctx: CanvasRenderingContext2D, t: number): void {
    const w = this.width;
    const h = this.height;
    ctx.save();
    for (const c of this.clouds) {
      const cw = c.w * w;
      const x = wrap(c.x * w + t * c.speed * w, -cw, w + cw);
      const y = c.y * h;
      ctx.globalAlpha = this.theme === 'homeland' ? 0.22 : 0.1;
      for (let k = 0; k < 5; k += 1) {
        const ox = (k - 2) * cw * 0.22;
        const oy = Math.sin(k * 1.7) * cw * 0.05;
        const r = cw * (0.28 + 0.06 * Math.cos(k * 2.1));
        ctx.drawImage(this.sprites.cloud, x + ox - r, y + oy - r * 0.45, r * 2, r * 0.9);
      }
    }
    ctx.restore();
  }

  private drawBirds(ctx: CanvasRenderingContext2D, t: number, color: string): void {
    const w = this.width;
    const h = this.height;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineCap = 'round';
    for (const b of this.birds) {
      const x = wrap(b.x * w + t * b.speed * w, -40, w + 40);
      const y = b.y * h + Math.sin(t * 0.7 + b.phase) * 8;
      const flap = Math.sin(t * 7 + b.phase);
      const s = b.size;
      ctx.lineWidth = Math.max(1, s * 0.22);
      ctx.beginPath();
      ctx.moveTo(x - s, y - flap * s * 0.6);
      ctx.quadraticCurveTo(x - s * 0.4, y - s * 0.25, x, y);
      ctx.quadraticCurveTo(x + s * 0.4, y - s * 0.25, x + s, y - flap * s * 0.6);
      ctx.stroke();
    }
    ctx.restore();
  }

  private drawMist(ctx: CanvasRenderingContext2D, t: number, y: number, alpha: number): void {
    const w = this.width;
    ctx.save();
    ctx.globalAlpha = alpha;
    for (let i = 0; i < 4; i += 1) {
      const mw = 0.7 * w;
      const x = wrap(i * 0.33 * w + t * (6 + i * 3), -mw, w + mw);
      ctx.drawImage(this.sprites.smokeLight, x - mw / 2, y - 0.03 * this.height + i * 6, mw, 0.08 * this.height);
    }
    ctx.restore();
  }

  private drawBlades(
    ctx: CanvasRenderingContext2D,
    list: Blade[],
    t: number,
    colors: [string, string, string],
    gustScale: number,
  ): void {
    const w = this.width;
    const h = this.height;
    ctx.save();
    ctx.lineCap = 'round';
    for (let shade = 0; shade < 3; shade += 1) {
      ctx.strokeStyle = colors[shade];
      ctx.lineWidth = Math.max(1.2, w / 900);
      ctx.beginPath();
      for (const b of list) {
        if (b.shade !== shade) continue;
        const x = b.x * w;
        const y = b.y * h;
        const len = b.len * h;
        // Gió thổi thành từng đợt lan ngang cánh đồng.
        const gust = 0.5 + 0.5 * Math.sin(t * 0.9 - b.x * 6);
        const sway = (Math.sin(t * 1.8 + b.phase + b.x * 9) * 0.25 + gust * 0.45) * len * gustScale;
        ctx.moveTo(x, y);
        ctx.quadraticCurveTo(x + sway * 0.3, y - len * 0.55, x + sway, y - len);
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  private drawBamboo(ctx: CanvasRenderingContext2D, t: number, color: string): void {
    const w = this.width;
    const h = this.height;
    ctx.save();
    for (const tree of this.trees) {
      const bx = tree.x * w;
      const by = 0.86 * h;
      const height = (0.42 + 0.12 * tree.scale) * h;
      const sway = Math.sin(t * 0.9 + tree.phase) * 14 * tree.scale + Math.sin(t * 2.3 + tree.phase) * 3;
      const tipX = bx + sway - 0.03 * w * (tree.x - 0.84) * 30;
      const tipY = by - height;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(3, w * 0.004 * tree.scale);
      ctx.beginPath();
      ctx.moveTo(bx, by);
      ctx.quadraticCurveTo(bx, by - height * 0.6, tipX, tipY);
      ctx.stroke();
      ctx.fillStyle = color;
      for (let k = 0; k < 9; k += 1) {
        const u = 0.45 + (k / 9) * 0.55;
        const px = lerp(bx, tipX, u * u);
        const py = lerp(by, tipY, u);
        const flutter = Math.sin(t * 3.1 + k + tree.phase) * 0.25;
        for (const dir of [-1, 1]) {
          ctx.beginPath();
          ctx.ellipse(px + dir * 12 * tree.scale, py + 4, 16 * tree.scale, 3 * tree.scale, dir * (0.5 + flutter), 0, TAU);
          ctx.fill();
        }
      }
    }
    ctx.restore();
  }

  private drawPines(ctx: CanvasRenderingContext2D, t: number, color: string): void {
    const w = this.width;
    const h = this.height;
    ctx.save();
    ctx.fillStyle = color;
    for (const tree of this.trees) {
      const bx = tree.x * w;
      const by = (0.82 + 0.06 * (tree.scale - 0.75)) * h;
      const height = (0.38 + 0.2 * tree.scale) * h;
      const skew = Math.sin(t * 0.6 + tree.phase) * 0.025 + Math.sin(t * 1.7 + tree.phase) * 0.006;
      ctx.save();
      ctx.transform(1, 0, skew, 1, -skew * by, 0);
      ctx.fillRect(bx - 3 * tree.scale, by - height * 0.3, 6 * tree.scale, height * 0.3);
      for (let k = 0; k < 6; k += 1) {
        const top = by - height + (k / 6) * height * 0.78;
        const half = (0.018 + 0.012 * k) * w * tree.scale;
        ctx.beginPath();
        ctx.moveTo(bx, top);
        ctx.lineTo(bx - half, top + height * 0.2);
        ctx.lineTo(bx + half, top + height * 0.2);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    }
    ctx.restore();
  }

  private drawTamarind(ctx: CanvasRenderingContext2D, t: number, color: string): void {
    const w = this.width;
    const h = this.height;
    const bx = 0.86 * w;
    const by = 0.86 * h;
    ctx.save();
    ctx.fillStyle = '#2a1e14';
    ctx.beginPath();
    ctx.moveTo(bx - 0.018 * w, by);
    ctx.quadraticCurveTo(bx - 0.01 * w, by - 0.22 * h, bx - 0.035 * w, by - 0.36 * h);
    ctx.lineTo(bx + 0.005 * w, by - 0.36 * h);
    ctx.quadraticCurveTo(bx + 0.01 * w, by - 0.2 * h, bx + 0.02 * w, by);
    ctx.closePath();
    ctx.fill();
    for (let k = 0; k < 13; k += 1) {
      const angle = (k / 13) * Math.PI + Math.PI;
      const r = 0.12 * w * (0.6 + 0.4 * Math.abs(Math.sin(k * 1.9)));
      const sway = Math.sin(t * 0.8 + k * 0.7) * 6 + Math.sin(t * 2.1 + k) * 2;
      const cx = bx - 0.015 * w + Math.cos(angle) * r * 0.9 + sway;
      const cy = by - 0.38 * h + Math.sin(angle) * r * 0.45;
      ctx.fillStyle = k % 3 === 0 ? '#3d4a24' : color;
      ctx.beginPath();
      ctx.arc(cx, cy, 0.05 * w * (0.7 + 0.3 * Math.cos(k * 2.3)), 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  // ───────────────────────── Bạch Đằng ─────────────────────────

  private depthY(z: number): number {
    const H0 = this.horizon();
    return H0 + (this.height - H0) * z ** 1.7;
  }

  private drawNaval(ctx: CanvasRenderingContext2D, t: number, dt: number, camera: BackdropCamera): void {
    const w = this.width;
    const h = this.height;
    const H0 = this.horizon();
    const tide = 0.5 + 0.5 * Math.sin((TAU * t) / 18); // 1 = triều lên, 0 = triều rút
    const fires: FirePoint[] = [];
    this.layer(ctx, camera, 0.3, () => {
      this.drawClouds(ctx, t);
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.28 + 0.08 * Math.sin(t * 3.1) + 0.05 * Math.sin(t * 7.3);
      ctx.drawImage(this.sprites.glow, 0.45 * w, H0 - 0.35 * h, 0.6 * w, 0.5 * h);
      ctx.restore();
      for (const f of this.farFires) {
        const x = f.x * w;
        const y = H0 - 0.005 * h;
        this.drawFire(ctx, x, y, 10 * f.size, t, f.seed);
        fires.push({ x, y: y - 8 * f.size, size: 1.6 * f.size, seed: f.seed });
      }
    });
    const reflections: FirePoint[] = [];
    this.layer(ctx, camera, 0.7, () => {
      const rows = 18;
      const rowZ = (j: number) => (j + 0.5) / rows;
      type Item = { z: number; paint: () => void };
      const items: Item[] = [];
      for (const stake of this.stakes) items.push({ z: stake.z, paint: () => this.drawStake(ctx, stake, t, tide) });
      for (const junk of this.junks) {
        items.push({ z: junk.z, paint: () => this.drawJunk(ctx, junk, t, tide, fires, reflections) });
      }
      for (const skiff of this.skiffs) items.push({ z: skiff.z, paint: () => this.drawSkiff(ctx, skiff, t) });
      items.sort((a, b) => a.z - b.z);
      let cursor = 0;
      for (let j = 0; j < rows; j += 1) {
        const z = rowZ(j);
        while (cursor < items.length && items[cursor].z <= z) {
          items[cursor].paint();
          cursor += 1;
        }
        this.drawWaveRow(ctx, j, rows, t, tide);
      }
      while (cursor < items.length) {
        items[cursor].paint();
        cursor += 1;
      }
      // Ánh lửa phản chiếu lăn tăn trên mặt nước.
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      for (const r of reflections) {
        const flicker = 0.35 + 0.15 * Math.sin(t * 9 + r.seed);
        ctx.globalAlpha = flicker;
        ctx.drawImage(this.sprites.glow, r.x - 40 * r.size, r.y - 6 * r.size, 80 * r.size, 26 * r.size);
      }
      ctx.restore();
    });
    this.updateSmokeAndEmbers(fires, t, dt, 12, 16);
    this.layer(ctx, camera, 0.7, () => {
      this.drawSmokeAndEmbers(ctx, t);
      this.drawArrows(ctx, t);
    });
    this.layer(ctx, camera, 1.1, () => {
      this.drawBanner(ctx, 0.07 * w, 0.98 * h, 0.42 * h, t, 0, '#b3261e');
      this.drawBanner(ctx, 0.17 * w, 1.0 * h, 0.34 * h, t, 1.7, '#c9a227');
      this.drawBlades(ctx, this.reeds, t, ['#120c0a', '#1a110d', '#0c0807'], 0.7);
    });
  }

  private drawWaveRow(ctx: CanvasRenderingContext2D, j: number, rows: number, t: number, tide: number): void {
    const w = this.width;
    const z = (j + 0.5) / rows;
    const y = this.depthY(z) + (tide - 0.5) * 3 * z;
    const yNext = this.depthY((j + 1.5) / rows);
    const amp = 1 + 9 * z ** 1.4;
    const lambda = 40 + 280 * z;
    const speed = 0.8 + 0.6 * z;
    const phase = j * 1.93;
    const step = Math.max(6, lambda / 8);
    const crest = (x: number) =>
      y +
      amp * Math.sin((x / lambda) * TAU + t * speed + phase) +
      amp * 0.4 * Math.sin((x / (lambda * 0.53)) * TAU - t * speed * 1.3 + phase * 2);
    const shade = Math.round(lerp(96, 24, z));
    ctx.beginPath();
    ctx.moveTo(-20, crest(-20));
    for (let x = -20 + step; x <= w + 20; x += step) ctx.lineTo(x, crest(x));
    ctx.lineTo(w + 20, yNext + amp * 2 + 4);
    ctx.lineTo(-20, yNext + amp * 2 + 4);
    ctx.closePath();
    ctx.fillStyle = `rgb(${shade + 10},${Math.round(shade * 0.58)},${Math.round(shade * 0.55)})`;
    ctx.fill();
    ctx.strokeStyle = `rgba(255,${150 + Math.round(40 * z)},90,${0.18 + 0.3 * (1 - z)})`;
    ctx.lineWidth = 0.8 + 1.4 * z;
    ctx.beginPath();
    for (let x = -20; x <= w + 20; x += step) {
      const yy = crest(x);
      // Chỉ tô sáng phần đỉnh sóng (độ dốc âm) → vệt lấp lánh rời rạc.
      if (Math.sin((x / lambda) * TAU + t * speed + phase) > 0.55) {
        ctx.moveTo(x, yy);
        ctx.lineTo(x + step * 0.8, crest(x + step * 0.8));
      }
    }
    ctx.stroke();
  }

  private drawStake(ctx: CanvasRenderingContext2D, stake: Stake, t: number, tide: number): void {
    const x = stake.x * this.width;
    const y = this.depthY(stake.z);
    const s = 0.35 + 1.3 * stake.z;
    const reveal = 0.25 + 0.75 * (1 - tide);
    const len = 80 * s * stake.height * reveal;
    const half = 5 * s;
    const tipX = x + Math.sin(stake.tilt) * len;
    const tipY = y - Math.cos(stake.tilt) * len;
    ctx.save();
    ctx.fillStyle = '#4a2c18';
    ctx.beginPath();
    ctx.moveTo(x - half, y + 2);
    ctx.lineTo(tipX - half * 0.25, tipY + 6 * s);
    ctx.lineTo(tipX, tipY);
    ctx.lineTo(tipX + half * 0.3, tipY + 6 * s);
    ctx.lineTo(x + half, y + 2);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = 'rgba(240,150,80,0.7)';
    ctx.lineWidth = Math.max(1, 1.4 * s);
    ctx.beginPath();
    ctx.moveTo(x + half * 0.6, y);
    ctx.lineTo(tipX + half * 0.2, tipY + 6 * s);
    ctx.stroke();
    ctx.globalAlpha = 0.25 + 0.15 * Math.sin(t * 3 + stake.x * 40);
    ctx.fillStyle = '#e8d2c0';
    ctx.beginPath();
    ctx.ellipse(x, y + 1, half * 2.4, half * 0.6, 0, 0, TAU);
    ctx.fill();
    ctx.restore();
  }

  private drawJunk(
    ctx: CanvasRenderingContext2D,
    junk: Junk,
    t: number,
    tide: number,
    fires: FirePoint[],
    reflections: FirePoint[],
  ): void {
    const s = 0.45 + 1.4 * junk.z;
    const L = 200 * s;
    const H = L * 0.16;
    const drift = Math.sin(t * 0.05 + junk.phase) * 0.02;
    const x = (junk.x + drift) * this.width;
    // Triều rút → thuyền mắc cọc, nghiêng dần (wreck).
    const stuck = junk.wreck * (1 - tide);
    const y = this.depthY(junk.z) + Math.sin(t * 1.3 + junk.phase) * 2.5 * s + stuck * 10 * s;
    const roll = Math.sin(t * 0.9 + junk.phase) * 0.035 + stuck * 0.35;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(roll);
    // Buồm cánh dơi có nẹp tre.
    for (const [mx, mh, sw] of [[-0.08, 0.62, 0.3], [0.2, 0.5, 0.24]] as Array<[number, number, number]>) {
      const bulge = Math.sin(t * 1.2 + junk.phase + mx * 5) * L * 0.02;
      const top = -H - L * mh;
      ctx.strokeStyle = '#1a0f0a';
      ctx.lineWidth = Math.max(1, 2.2 * s);
      ctx.beginPath();
      ctx.moveTo(mx * L, -H * 0.8);
      ctx.lineTo(mx * L, top - 6 * s);
      ctx.stroke();
      ctx.fillStyle = junk.burning ? '#4a2a18' : '#6b4a2c';
      ctx.beginPath();
      ctx.moveTo(mx * L, top);
      ctx.quadraticCurveTo(mx * L - sw * L * 0.55 + bulge, top + L * mh * 0.5, mx * L - sw * L * 0.4, -H * 1.1);
      ctx.lineTo(mx * L + sw * L * 0.12, -H * 1.1);
      ctx.lineTo(mx * L + sw * L * 0.1, top);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = 'rgba(20,10,6,0.7)';
      ctx.lineWidth = Math.max(0.6, s);
      for (let k = 1; k < 6; k += 1) {
        const yy = lerp(top, -H * 1.1, k / 6);
        ctx.beginPath();
        ctx.moveTo(mx * L - sw * L * 0.45 + bulge * 0.5, yy);
        ctx.lineTo(mx * L + sw * L * 0.11, yy);
        ctx.stroke();
      }
    }
    // Thân thuyền: mũi thấp, đuôi lầu cao.
    ctx.fillStyle = '#22140d';
    ctx.beginPath();
    ctx.moveTo(-L * 0.5, -H * 0.9);
    ctx.lineTo(-L * 0.38, 0);
    ctx.lineTo(L * 0.36, 0);
    ctx.lineTo(L * 0.5, -H * 1.5);
    ctx.lineTo(L * 0.3, -H * 1.2);
    ctx.lineTo(-L * 0.3, -H * 0.75);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = 'rgba(230,120,60,0.55)';
    ctx.lineWidth = Math.max(1, 1.4 * s);
    ctx.beginPath();
    ctx.moveTo(-L * 0.5, -H * 0.9);
    ctx.lineTo(-L * 0.3, -H * 0.75);
    ctx.lineTo(L * 0.3, -H * 1.2);
    ctx.lineTo(L * 0.5, -H * 1.5);
    ctx.stroke();
    this.drawFlag(ctx, L * 0.48, -H * 1.5, 26 * s, 12 * s, t, junk.phase, '#20262e');
    const localFires: Array<[number, number, number]> = junk.burning
      ? [[-L * 0.14, -H * 0.85, 1], [L * 0.22, -H * 1.25, 1.2], [-L * 0.05, -H - L * 0.25, 0.7]]
      : [];
    for (const [fx, fy, size] of localFires) {
      this.drawFire(ctx, fx, fy, 14 * s * size, t, junk.phase * 10 + fx);
      const c = Math.cos(roll);
      const sn = Math.sin(roll);
      const wx = x + fx * c - fy * sn;
      const wy = y + fx * sn + fy * c;
      fires.push({ x: wx, y: wy - 10 * s * size, size: s * size, seed: junk.phase + fx });
    }
    ctx.restore();
    if (junk.burning) reflections.push({ x, y: y + 6 * s, size: s, seed: junk.phase });
  }

  private drawSkiff(ctx: CanvasRenderingContext2D, skiff: Skiff, t: number): void {
    const w = this.width;
    const s = 0.3 + 1.2 * skiff.z;
    const L = 120 * s;
    const x = wrap(skiff.x * w + t * skiff.speed * w, -0.15 * w, 1.15 * w);
    const y = this.depthY(skiff.z) + Math.sin(t * 2 + skiff.phase) * 2 * s;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.sin(t * 1.6 + skiff.phase) * 0.03);
    ctx.fillStyle = '#1b120c';
    ctx.beginPath();
    ctx.moveTo(-L * 0.5, -L * 0.07);
    ctx.quadraticCurveTo(0, L * 0.06, L * 0.55, -L * 0.1);
    ctx.lineTo(L * 0.4, -L * 0.04);
    ctx.lineTo(-L * 0.38, -L * 0.03);
    ctx.closePath();
    ctx.fill();
    for (let k = 0; k < 5; k += 1) {
      const rx = -L * 0.3 + k * L * 0.15;
      const stroke = Math.sin(t * 4.2 + skiff.phase - k * 0.4);
      ctx.fillStyle = '#120c08';
      ctx.beginPath();
      ctx.arc(rx + stroke * 2 * s, -L * 0.1, 3.2 * s, 0, TAU);
      ctx.fill();
      ctx.fillRect(rx - 2 * s + stroke * 1.5 * s, -L * 0.1, 4 * s, L * 0.06);
      ctx.strokeStyle = '#3a2a1a';
      ctx.lineWidth = Math.max(0.8, 1.3 * s);
      ctx.beginPath();
      ctx.moveTo(rx, -L * 0.06);
      ctx.lineTo(rx - L * 0.12 - stroke * L * 0.06, L * 0.06);
      ctx.stroke();
    }
    ctx.strokeStyle = '#1a0f0a';
    ctx.lineWidth = Math.max(1, 1.6 * s);
    ctx.beginPath();
    ctx.moveTo(-L * 0.4, -L * 0.05);
    ctx.lineTo(-L * 0.4, -L * 0.42);
    ctx.stroke();
    this.drawFlag(ctx, -L * 0.4, -L * 0.42, 24 * s, 14 * s, t, skiff.phase, '#b3261e');
    ctx.restore();
  }

  // ───────────────────────── Trận trên bộ ─────────────────────────

  private drawLand(ctx: CanvasRenderingContext2D, t: number, dt: number, camera: BackdropCamera): void {
    const w = this.width;
    const h = this.height;
    const H0 = this.horizon();
    const fires: FirePoint[] = [];
    this.layer(ctx, camera, 0.3, () => {
      this.drawClouds(ctx, t);
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.22 + 0.06 * Math.sin(t * 2.7);
      ctx.drawImage(this.sprites.glow, 0.35 * w, H0 - 0.3 * h, 0.65 * w, 0.42 * h);
      ctx.restore();
    });
    this.layer(ctx, camera, 0.55, () => {
      // Đồn lũy bằng gỗ đang cháy trên sườn đồi.
      for (const f of this.farFires) {
        const x = f.x * w;
        const y = H0 + 0.015 * h;
        ctx.fillStyle = '#1a100c';
        for (let k = -4; k <= 4; k += 1) {
          ctx.fillRect(x + k * 7 * f.size, y - 22 * f.size, 4 * f.size, 22 * f.size);
          ctx.beginPath();
          ctx.moveTo(x + k * 7 * f.size, y - 22 * f.size);
          ctx.lineTo(x + k * 7 * f.size + 2 * f.size, y - 28 * f.size);
          ctx.lineTo(x + k * 7 * f.size + 4 * f.size, y - 22 * f.size);
          ctx.fill();
        }
        this.drawFire(ctx, x, y - 10 * f.size, 22 * f.size, t, f.seed);
        fires.push({ x, y: y - 30 * f.size, size: 1.8 * f.size, seed: f.seed });
      }
      this.drawMist(ctx, t, H0 + 0.04 * h, 0.12);
    });
    this.layer(ctx, camera, 0.75, () => this.drawSoldiers(ctx, t));
    this.updateSmokeAndEmbers(fires, t, dt, 10, 12);
    this.layer(ctx, camera, 0.55, () => {
      this.drawSmokeAndEmbers(ctx, t);
      this.drawArrows(ctx, t);
    });
    this.layer(ctx, camera, 1.1, () => {
      this.drawBanner(ctx, 0.07 * w, 0.98 * h, 0.44 * h, t, 0.4, '#b3261e');
      this.drawBanner(ctx, 0.9 * w, 1.0 * h, 0.36 * h, t, 2.1, '#c9a227');
      this.drawBlades(ctx, this.blades, t, ['#1c140e', '#251a12', '#140e0a'], 0.8);
    });
  }

  private drawSoldiers(ctx: CanvasRenderingContext2D, t: number): void {
    const w = this.width;
    const h = this.height;
    const H0 = this.horizon();
    ctx.save();
    for (const soldier of this.soldiers) {
      const near = soldier.rank === 1;
      const s = near ? 1.25 : 0.75;
      const speed = near ? 0.018 : 0.012;
      const x = wrap(soldier.x * w + t * speed * w, -0.05 * w, 1.05 * w);
      const baseY = (near ? H0 + 0.13 * h : H0 + 0.06 * h) + Math.sin(soldier.x * 31) * 4;
      const bob = Math.abs(Math.sin(t * 6 + soldier.phase)) * 2.5 * s;
      const stride = Math.sin(t * 6 + soldier.phase) * 4 * s;
      const top = baseY - 30 * s - bob;
      ctx.fillStyle = near ? '#120c0a' : '#1f1612';
      ctx.strokeStyle = ctx.fillStyle;
      ctx.lineWidth = 2.2 * s;
      ctx.beginPath();
      ctx.moveTo(x, baseY - 12 * s - bob);
      ctx.lineTo(x - stride, baseY);
      ctx.moveTo(x, baseY - 12 * s - bob);
      ctx.lineTo(x + stride, baseY);
      ctx.stroke();
      ctx.fillRect(x - 4 * s, top + 6 * s, 8 * s, 14 * s);
      ctx.beginPath();
      ctx.arc(x, top + 3 * s, 3.4 * s, 0, TAU);
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(x, top + 1 * s, 6 * s, 1.6 * s, 0, 0, TAU);
      ctx.fill();
      ctx.lineWidth = 1.2 * s;
      ctx.beginPath();
      ctx.moveTo(x + 5 * s, baseY - 6 * s - bob);
      ctx.lineTo(x + 12 * s, top - 18 * s);
      ctx.stroke();
      if (soldier.shield) {
        ctx.beginPath();
        ctx.ellipse(x + 4 * s, top + 13 * s, 3 * s, 7 * s, 0, 0, TAU);
        ctx.fill();
      }
      if (soldier.banner) this.drawFlag(ctx, x - 2 * s, top - 30 * s, 22 * s, 13 * s, t, soldier.phase, '#b3261e');
    }
    ctx.restore();
  }

  // ───────────────────────── Điện / sự kiện ─────────────────────────

  private drawHall(ctx: CanvasRenderingContext2D, t: number, dt: number, camera: BackdropCamera): void {
    const w = this.width;
    const h = this.height;
    this.layer(ctx, camera, 0.4, () => {
      // Tia nắng xiên qua cửa song.
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      for (const [cx, phase] of [[0.25, 0], [0.75, 1.9]] as Array<[number, number]>) {
        const alpha = 0.1 + 0.04 * Math.sin(t * 0.7 + phase) + 0.02 * Math.sin(t * 2.3 + phase);
        const g = ctx.createLinearGradient(cx * w, 0.2 * h, cx * w + 0.12 * w, 0.95 * h);
        g.addColorStop(0, `rgba(255,205,140,${alpha * 2})`);
        g.addColorStop(1, 'rgba(255,205,140,0)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.moveTo(cx * w - 0.08 * w, 0.2 * h);
        ctx.lineTo(cx * w + 0.08 * w, 0.2 * h);
        ctx.lineTo(cx * w + 0.26 * w, 0.98 * h);
        ctx.lineTo(cx * w - 0.02 * w, 0.98 * h);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    });
    this.layer(ctx, camera, 0.7, () => {
      for (const lantern of this.lanterns) this.drawLantern(ctx, lantern, t);
      // Nến trên hương án.
      for (const cx of [0.62, 0.84]) this.drawFire(ctx, cx * w, 0.775 * h, 9, t, cx * 50);
      ctx.fillStyle = '#c9a24a';
      for (const cx of [0.62, 0.84]) ctx.fillRect(cx * w - 3, 0.775 * h, 6, 0.02 * h);
      // Lư hương.
      ctx.fillStyle = '#6a4a1e';
      ctx.beginPath();
      ctx.ellipse(0.73 * w, 0.775 * h, 0.03 * w, 0.012 * h, 0, 0, TAU);
      ctx.fill();
      ctx.fillRect(0.73 * w - 0.022 * w, 0.765 * h, 0.044 * w, 0.012 * h);
    });
    // Khói trầm uốn lượn.
    const rng = Math.random;
    this.emitAccumulator += dt * 7;
    while (this.emitAccumulator >= 1) {
      this.emitAccumulator -= 1;
      this.smoke.spawn({
        x: 0.73 * w + (rng() - 0.5) * 6,
        y: 0.76 * h,
        vx: 0,
        vy: -(26 + rng() * 10),
        size: 3,
        grow: 7,
        life: 0,
        maxLife: 5 + rng() * 2,
        seed: rng() * TAU,
      });
    }
    this.smoke.update(dt, (p) => {
      p.vx = Math.sin(t * 1.2 + p.y * 0.02 + p.seed * 0.3) * 14;
    });
    for (let i = 0; i < 2; i += 1) {
      this.motes.spawn({
        x: (0.15 + rng() * 0.75) * w,
        y: (0.25 + rng() * 0.6) * h,
        vx: (rng() - 0.5) * 6,
        vy: (rng() - 0.5) * 4,
        size: 0.8 + rng() * 1.4,
        grow: 0,
        life: 0,
        maxLife: 4 + rng() * 4,
        seed: rng() * TAU,
      });
      if (rng() < 0.6) break;
    }
    this.motes.update(dt, () => undefined);
    this.layer(ctx, camera, 0.8, () => {
      ctx.save();
      for (const p of this.smoke.items) {
        const k = p.life / p.maxLife;
        ctx.globalAlpha = Math.min(1, p.life * 3) * (1 - k) * 0.35;
        ctx.drawImage(this.sprites.smokeLight, p.x - p.size, p.y - p.size, p.size * 2, p.size * 2);
      }
      ctx.globalCompositeOperation = 'lighter';
      for (const p of this.motes.items) {
        ctx.globalAlpha = Math.sin(Math.PI * (p.life / p.maxLife)) * 0.6;
        ctx.fillStyle = '#ffe2b0';
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, TAU);
        ctx.fill();
      }
      ctx.restore();
    });
  }

  private drawLantern(ctx: CanvasRenderingContext2D, lantern: Lantern, t: number): void {
    const w = this.width;
    const h = this.height;
    const angle = Math.sin(t * 1.1 + lantern.phase) * 0.07 + Math.sin(t * 2.7 + lantern.phase) * 0.012;
    const px = lantern.x * w;
    const len = lantern.length * h;
    const cx = px + Math.sin(angle) * len;
    const cy = Math.cos(angle) * len;
    const r = 0.022 * w * lantern.size;
    ctx.save();
    ctx.strokeStyle = '#1a0d08';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(cx, cy);
    ctx.stroke();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.45 + 0.1 * Math.sin(t * 5 + lantern.phase);
    ctx.drawImage(this.sprites.glow, cx - r * 4, cy + r - r * 4, r * 8, r * 8);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.translate(cx, cy);
    ctx.rotate(-angle);
    const body = ctx.createRadialGradient(0, r * 1.1, r * 0.1, 0, r * 1.1, r * 1.2);
    body.addColorStop(0, '#ff9a4a');
    body.addColorStop(0.6, '#c0301c');
    body.addColorStop(1, '#5a0e08');
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.ellipse(0, r * 1.1, r * 0.95, r * 1.1, 0, 0, TAU);
    ctx.fill();
    ctx.fillStyle = '#c9a24a';
    ctx.fillRect(-r * 0.5, 0, r, r * 0.18);
    ctx.fillRect(-r * 0.5, r * 2.05, r, r * 0.18);
    ctx.strokeStyle = '#c9a24a';
    ctx.lineWidth = 1;
    const tassel = Math.sin(t * 3 + lantern.phase) * r * 0.15;
    ctx.beginPath();
    ctx.moveTo(0, r * 2.2);
    ctx.lineTo(tassel, r * 3);
    ctx.stroke();
    ctx.restore();
  }

  // ───────────────────────── Thành phần dùng chung ─────────────────────────

  private drawFire(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, t: number, seed: number): void {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.5 + 0.2 * Math.sin(t * 11 + seed) + 0.1 * Math.sin(t * 23 + seed * 2);
    ctx.drawImage(this.sprites.glow, x - size * 2.2, y - size * 2.6, size * 4.4, size * 4.4);
    const tongues: Array<[string, number, number]> = [
      ['rgba(255,90,30,0.55)', 1, 0.42],
      ['rgba(255,160,50,0.65)', 0.72, 0.3],
      ['rgba(255,236,150,0.8)', 0.42, 0.16],
    ];
    for (const [color, hScale, wScale] of tongues) {
      ctx.globalAlpha = 1;
      ctx.fillStyle = color;
      for (let k = 0; k < 3; k += 1) {
        const flick = Math.sin(t * (9 + k * 2.3) + seed + k * 1.9);
        const fh = size * hScale * (1.3 + 0.35 * flick);
        const fw = size * wScale;
        const ox = (k - 1) * fw * 0.6;
        const sway = Math.sin(t * 6 + seed + k) * size * 0.14;
        ctx.beginPath();
        ctx.moveTo(x + ox - fw, y);
        ctx.quadraticCurveTo(x + ox - fw * 0.9, y - fh * 0.55, x + ox + sway, y - fh);
        ctx.quadraticCurveTo(x + ox + fw * 0.9, y - fh * 0.55, x + ox + fw, y);
        ctx.closePath();
        ctx.fill();
      }
    }
    ctx.restore();
  }

  private updateSmokeAndEmbers(fires: FirePoint[], t: number, dt: number, smokeRate: number, emberRate: number): void {
    const rng = Math.random;
    const wind = 10 + 6 * Math.sin(t * 0.2);
    for (const f of fires) {
      const smokeCount = smokeRate * dt * f.size + (rng() < (smokeRate * dt * f.size) % 1 ? 1 : 0);
      for (let i = 0; i < Math.floor(smokeCount); i += 1) {
        this.smoke.spawn({
          x: f.x + (rng() - 0.5) * 10 * f.size,
          y: f.y,
          vx: wind * (0.5 + rng()),
          vy: -(16 + rng() * 22) * Math.max(0.6, f.size),
          size: 6 * f.size,
          grow: (12 + rng() * 8) * f.size,
          life: 0,
          maxLife: 3.5 + rng() * 3,
          seed: rng() * TAU,
        });
      }
      if (rng() < emberRate * dt * f.size) {
        this.embers.spawn({
          x: f.x + (rng() - 0.5) * 12 * f.size,
          y: f.y + 6,
          vx: wind * 0.6 + (rng() - 0.5) * 30,
          vy: -(40 + rng() * 60),
          size: 1 + rng() * 1.8,
          grow: 0,
          life: 0,
          maxLife: 1.4 + rng() * 1.8,
          seed: rng() * TAU,
        });
      }
    }
    this.smoke.update(dt, (p) => {
      p.vy *= 1 - 0.18 * dt;
    });
    this.embers.update(dt, (p) => {
      p.vx += Math.sin(t * 4 + p.seed) * 40 * dt;
      p.vy += 12 * dt;
    });
  }

  private drawSmokeAndEmbers(ctx: CanvasRenderingContext2D, t: number): void {
    ctx.save();
    for (const p of this.smoke.items) {
      const k = p.life / p.maxLife;
      const alpha = Math.min(1, p.life * 3) * (1 - k) ** 1.2 * 0.5;
      ctx.globalAlpha = alpha;
      const sprite = p.life < 0.7 ? this.sprites.smokeWarm : this.sprites.smoke;
      ctx.drawImage(sprite, p.x - p.size, p.y - p.size, p.size * 2, p.size * 2);
    }
    ctx.globalCompositeOperation = 'lighter';
    for (const p of this.embers.items) {
      const k = p.life / p.maxLife;
      ctx.globalAlpha = (1 - k) * (0.6 + 0.4 * Math.sin(t * 20 + p.seed));
      ctx.fillStyle = '#ffb347';
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  private drawArrows(ctx: CanvasRenderingContext2D, t: number): void {
    const w = this.width;
    const h = this.height;
    ctx.save();
    ctx.lineCap = 'round';
    for (const a of this.arrows) {
      const u = (((t + a.offset) % a.period) + a.period) % a.period / a.period;
      if (u > 0.55) continue;
      const f = u / 0.55;
      const pos = (q: number): [number, number] => [
        lerp(a.sx, a.tx, q) * w,
        lerp(a.sy, a.ty, q) * h - Math.sin(Math.PI * q) * a.arc * h,
      ];
      const [x, y] = pos(f);
      const [x2, y2] = pos(Math.min(1, f + 0.01));
      const angle = Math.atan2(y2 - y, x2 - x);
      const scale = lerp(1.2, 0.45, f);
      const len = 30 * scale;
      const bx = x - Math.cos(angle) * len;
      const by = y - Math.sin(angle) * len;
      ctx.globalAlpha = Math.min(1, (1 - f) * 4) * 0.85;
      ctx.strokeStyle = '#d8c7a8';
      ctx.lineWidth = 1.4 * scale;
      ctx.beginPath();
      ctx.moveTo(bx, by);
      ctx.lineTo(x, y);
      ctx.moveTo(bx, by);
      ctx.lineTo(bx - Math.cos(angle - 0.5) * 6 * scale, by - Math.sin(angle - 0.5) * 6 * scale);
      ctx.moveTo(bx, by);
      ctx.lineTo(bx - Math.cos(angle + 0.5) * 6 * scale, by - Math.sin(angle + 0.5) * 6 * scale);
      ctx.stroke();
      if (a.flaming) {
        ctx.globalCompositeOperation = 'lighter';
        ctx.drawImage(this.sprites.glow, x - 9 * scale, y - 9 * scale, 18 * scale, 18 * scale);
        ctx.globalCompositeOperation = 'source-over';
      }
    }
    ctx.restore();
  }

  /** Cờ vải: dải lưới gợn sóng theo gió, sóng chạy từ cán ra đuôi cờ. */
  private drawFlag(
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    fw: number,
    fh: number,
    t: number,
    phase: number,
    color: string,
  ): void {
    const n = 10;
    const top: Array<[number, number]> = [];
    const bottom: Array<[number, number]> = [];
    for (let i = 0; i <= n; i += 1) {
      const u = i / n;
      const wave = Math.sin(u * 5 - t * 6 + phase) * fh * 0.22 * u;
      top.push([x + u * fw, y + wave]);
      bottom.push([x + u * fw * 0.96, y + fh + wave * 1.1]);
    }
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(top[0][0], top[0][1]);
    for (const [px, py] of top) ctx.lineTo(px, py);
    for (let i = bottom.length - 1; i >= 0; i -= 1) ctx.lineTo(bottom[i][0], bottom[i][1]);
    ctx.closePath();
    ctx.fill();
  }

  private drawBanner(
    ctx: CanvasRenderingContext2D,
    x: number,
    baseY: number,
    height: number,
    t: number,
    phase: number,
    color: string,
  ): void {
    const sway = Math.sin(t * 0.8 + phase) * height * 0.01;
    ctx.save();
    ctx.strokeStyle = '#140c08';
    ctx.lineWidth = Math.max(3, height * 0.014);
    ctx.beginPath();
    ctx.moveTo(x, baseY);
    ctx.lineTo(x + sway, baseY - height);
    ctx.stroke();
    const fw = height * 0.42;
    const fh = height * 0.26;
    this.drawFlag(ctx, x + sway, baseY - height + 6, fw, fh, t, phase, color);
    ctx.strokeStyle = 'rgba(232,196,90,0.85)';
    ctx.lineWidth = Math.max(1.5, height * 0.006);
    ctx.beginPath();
    ctx.moveTo(x + sway + fw * 0.06, baseY - height + 6 + fh * 0.5);
    for (let i = 1; i <= 10; i += 1) {
      const u = i / 10;
      ctx.lineTo(x + sway + u * fw * 0.94, baseY - height + 6 + fh * 0.5 + Math.sin(u * 5 - t * 6 + phase) * fh * 0.22 * u);
    }
    ctx.stroke();
    ctx.restore();
  }

  dispose(): void {
    this.smoke.clear();
    this.embers.clear();
    this.motes.clear();
    this.staticLayer = null;
    this.external = null;
  }
}
