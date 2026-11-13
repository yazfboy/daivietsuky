/**
 * Rig 2.5D trên chính ảnh gốc (port HeroRigRenderer — Phase 2), dạng LBS thuận cho WebGL.
 *
 * Lưới tam giác phủ ảnh mẫu; mỗi đỉnh có trọng số xương (thân, đầu, cánh tay trên/dưới hai bên) và hệ số hàm
 * dưới — tính MỘT lần từ ``rig.json``. Mỗi khung chỉ cần trộn ma trận: p' = Σ w_b·M_b·(p + Δ_hàm(p)).
 * Kết cấu (texture) luôn là ảnh gốc → tư thế nghỉ hiển thị đúng ảnh gốc; chỉ hành động thay đổi.
 */
import { HeroJoints, HeroRigData } from '../types/hero';
import { DPFrameInterpolator, KeyPose } from './dpInterpolation';

export type Point = [number, number];
type Affine = [number, number, number, number, number, number]; // x' = a·x + b·y + c ; y' = d·x + e·y + f

export interface RigPose {
  bodyDx: number;
  bodyDy: number;
  lean: number;
  breath: number;
  headTilt: number;
  headNod: number;
  upperL: number;
  lowerL: number;
  upperR: number;
  lowerR: number;
  jawOpen: number;
  mouthWidth: number;
}

export const POSE_FIELDS: Array<keyof RigPose> = [
  'bodyDx', 'bodyDy', 'lean', 'breath', 'headTilt', 'headNod', 'upperL', 'lowerL', 'upperR', 'lowerR', 'jawOpen', 'mouthWidth',
];
const POSE_WEIGHTS = [25, 25, 0.25, 100, 0.3, 40, 0.05, 0.05, 0.05, 0.05, 0, 0];

export const REST_POSE: RigPose = {
  bodyDx: 0, bodyDy: 0, lean: 0, breath: 0, headTilt: 0, headNod: 0,
  upperL: 0, lowerL: 0, upperR: 0, lowerR: 0, jawOpen: 0, mouthWidth: 1,
};

const pose = (partial: Partial<RigPose>): RigPose => ({ ...REST_POSE, ...partial });
const toVector = (p: RigPose): number[] => POSE_FIELDS.map((f) => p[f]);
const fromVector = (v: number[]): RigPose =>
  POSE_FIELDS.reduce((acc, f, i) => ({ ...acc, [f]: v[i] }), { ...REST_POSE });

export function mirrorPose(p: RigPose): RigPose {
  return {
    ...p,
    bodyDx: -p.bodyDx, lean: -p.lean, headTilt: -p.headTilt,
    upperL: -p.upperR, lowerL: -p.lowerR, upperR: -p.upperL, lowerR: -p.lowerL,
  };
}

// Biên độ nhỏ: biến dạng chỉ "uốn" nhẹ ảnh gốc; cảm giác hành động đến từ toàn thân + cảnh nền.
const TALK_KEYS: KeyPose[] = [
  { values: toVector(REST_POSE), holdS: 0.4 },
  { values: toVector(pose({ breath: 0.006, headTilt: -1.6, headNod: 0.008, upperR: 1.5, lowerR: 3 })), holdS: 0.3 },
  { values: toVector(pose({ breath: 0.003, headTilt: 1.2, headNod: -0.004, upperL: -1.5, lowerL: -3 })), holdS: 0.3 },
  { values: toVector(pose({ breath: 0.007, headTilt: -0.5, headNod: 0.004 })), holdS: 0.2 },
];
const ACTION_KEYS: KeyPose[] = [
  { values: toVector(pose({ lean: -1, breath: 0.004, upperR: 6, lowerR: 4 })), holdS: 0.15 },
  { values: toVector(pose({ lean: -3.5, bodyDy: -0.02, headTilt: -2.5, upperR: 22, lowerR: 12, breath: 0.009 })), holdS: 0.35 },
  { values: toVector(pose({ lean: -6, bodyDx: 0.05, bodyDy: 0.005, headTilt: 2, upperR: 16, lowerR: -4, breath: 0.006 })), holdS: 0.3 },
  { values: toVector(pose({ lean: -2, bodyDx: 0.015, upperR: 9, lowerR: 6, breath: 0.003 })), holdS: 0.1 },
];

/** Biên đạo: chuỗi tư thế vòng lặp 60 FPS dựng bằng DP Frame Interpolation. */
export function buildChoreography(kind: 'talk' | 'action', side: 'right' | 'left' = 'right'): RigPose[] {
  const interpolator = new DPFrameInterpolator({ weights: POSE_WEIGHTS, holdWeight: 12 });
  const frames = kind === 'talk' ? interpolator.run(TALK_KEYS, 6) : interpolator.run(ACTION_KEYS, 4);
  const poses = frames.map(fromVector);
  return kind === 'action' && side === 'left' ? poses.map(mirrorPose) : poses;
}

const mul = (m: Affine, n: Affine): Affine => [
  m[0] * n[0] + m[1] * n[3], m[0] * n[1] + m[1] * n[4], m[0] * n[2] + m[1] * n[5] + m[2],
  m[3] * n[0] + m[4] * n[3], m[3] * n[1] + m[4] * n[4], m[3] * n[2] + m[4] * n[5] + m[5],
];
const translate = (x: number, y: number): Affine => [1, 0, x, 0, 1, y];
const rotate = (deg: number): Affine => {
  const r = (deg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [c, s, 0, -s, c, 0]; // ngược chiều kim đồng hồ trên màn hình (trục y hướng xuống)
};
const about = (p: Point, m: Affine): Affine => mul(translate(p[0], p[1]), mul(m, translate(-p[0], -p[1])));
const apply = (m: Affine, x: number, y: number): Point => [m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const smooth01 = (v: number) => {
  const x = clamp(v, 0, 1);
  return x * x * (3 - 2 * x);
};
const falloff = (d: number, inner: number, outer: number) =>
  d <= inner ? 1 : d >= outer ? 0 : 1 - smooth01((d - inner) / (outer - inner));
function segDist(px: number, py: number, a: Point, b: Point): number {
  const vx = b[0] - a[0];
  const vy = b[1] - a[1];
  const len2 = vx * vx + vy * vy;
  const t = len2 <= 1e-9 ? 0 : clamp(((px - a[0]) * vx + (py - a[1]) * vy) / len2, 0, 1);
  return Math.hypot(px - a[0] - t * vx, py - a[1] - t * vy);
}

const JOINT_NAMES: Array<keyof HeroJoints> = [
  'head_top', 'chin', 'neck', 'shoulder_l', 'shoulder_r', 'elbow_l', 'elbow_r', 'wrist_l', 'wrist_r', 'pelvis',
  'upper_lip', 'lower_lip', 'mouth_l', 'mouth_r',
];

const finitePoint = (p: unknown): p is Point =>
  Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]);

/** Khung xương theo tỉ lệ chuẩn chân dung bán thân — chỉ dùng khi rig.json thiếu/hỏng. */
export function proportionalRig(characterId: HeroRigData['character_id'], width: number, height: number): HeroRigData {
  const P = (x: number, y: number): Point => [x * width, y * height];
  return {
    character_id: characterId,
    width,
    height,
    joints: {
      head_top: P(0.5, 0.04), chin: P(0.5, 0.27), neck: P(0.5, 0.3),
      shoulder_l: P(0.38, 0.33), shoulder_r: P(0.62, 0.33),
      elbow_l: P(0.3, 0.52), elbow_r: P(0.7, 0.52),
      wrist_l: P(0.36, 0.66), wrist_r: P(0.64, 0.66),
      pelvis: P(0.5, 0.86),
      upper_lip: P(0.5, 0.215), lower_lip: P(0.5, 0.232), mouth_l: P(0.47, 0.224), mouth_r: P(0.53, 0.224),
    },
    face_box: [0.42 * width, 0.1 * height, 0.58 * width, 0.27 * height],
    mouth_box: [0.46 * width, 0.205 * height, 0.54 * width, 0.245 * height],
    source: 'proportional',
  };
}

/**
 * Làm sạch rig: thay khớp thiếu/không hữu hạn, kẹp vào khung ảnh, sửa thứ tự giải phẫu
 * (vai không cao hơn cổ, khuỷu dưới vai, khung mặt/miệng hợp lệ) — ví dụ tranh ngồi của Trần Hưng Đạo.
 */
export function sanitizeRig(
  rig: HeroRigData | null | undefined,
  characterId: HeroRigData['character_id'],
  imageWidth: number,
  imageHeight: number,
): HeroRigData {
  const width = imageWidth > 0 ? imageWidth : rig?.width ?? 720;
  const height = imageHeight > 0 ? imageHeight : rig?.height ?? 960;
  const fallback = proportionalRig(characterId, width, height);
  if (!rig || !rig.joints) return fallback;
  const sx = rig.width > 0 ? width / rig.width : 1;
  const sy = rig.height > 0 ? height / rig.height : 1;
  const joints = { ...fallback.joints };
  for (const name of JOINT_NAMES) {
    const p = rig.joints[name] as unknown;
    if (finitePoint(p)) joints[name] = [clamp(p[0] * sx, 0, width), clamp(p[1] * sy, 0, height)];
  }
  const box = (b: unknown, fb: [number, number, number, number]): [number, number, number, number] => {
    if (!Array.isArray(b) || b.length < 4 || !b.slice(0, 4).every(Number.isFinite)) return fb;
    const x0 = clamp(Math.min(b[0], b[2]) * sx, 0, width);
    const x1 = clamp(Math.max(b[0], b[2]) * sx, 0, width);
    const y0 = clamp(Math.min(b[1], b[3]) * sy, 0, height);
    const y1 = clamp(Math.max(b[1], b[3]) * sy, 0, height);
    return x1 - x0 < 4 || y1 - y0 < 4 ? fb : [x0, y0, x1, y1];
  };
  const faceBox = box(rig.face_box, fallback.face_box);
  const mouthBox = box(rig.mouth_box, fallback.mouth_box);
  const fh = faceBox[3] - faceBox[1];
  if (joints.chin[1] <= joints.head_top[1]) joints.chin = [joints.chin[0], faceBox[3]];
  if (joints.neck[1] < joints.chin[1]) joints.neck = [joints.neck[0], joints.chin[1] + 0.05 * fh];
  for (const side of ['l', 'r'] as const) {
    const shoulder = `shoulder_${side}` as const;
    const elbow = `elbow_${side}` as const;
    const wrist = `wrist_${side}` as const;
    if (joints[shoulder][1] < joints.neck[1] + 0.12 * fh) joints[shoulder] = [joints[shoulder][0], joints.neck[1] + 0.12 * fh];
    if (joints[elbow][1] < joints[shoulder][1] + 0.3 * fh) joints[elbow] = [joints[elbow][0], joints[shoulder][1] + 0.3 * fh];
    if (Math.hypot(joints[wrist][0] - joints[elbow][0], joints[wrist][1] - joints[elbow][1]) < 0.2 * fh) {
      joints[wrist] = [joints[elbow][0], joints[elbow][1] + 0.6 * fh];
    }
  }
  if (joints.pelvis[1] < joints.neck[1] + fh) joints.pelvis = [joints.pelvis[0], Math.min(height, joints.neck[1] + 3 * fh)];
  if (joints.lower_lip[1] <= joints.upper_lip[1]) joints.lower_lip = [joints.lower_lip[0], joints.upper_lip[1] + 0.08 * fh];
  if (joints.mouth_r[0] <= joints.mouth_l[0]) {
    joints.mouth_l = [mouthBox[0] + 0.15 * (mouthBox[2] - mouthBox[0]), joints.mouth_l[1]];
    joints.mouth_r = [mouthBox[2] - 0.15 * (mouthBox[2] - mouthBox[0]), joints.mouth_r[1]];
  }
  return { character_id: characterId, width, height, joints, face_box: faceBox, mouth_box: mouthBox, source: rig.source };
}

/** Lưới đã gắn xương của một vị anh hùng (toạ độ pixel ảnh gốc). */
export class SkinnedHeroMesh {
  readonly width: number;
  readonly height: number;
  readonly columns: number;
  readonly rows: number;
  /** Toạ độ nghỉ (x, y) từng đỉnh. */
  readonly rest: Float32Array;
  /** Toạ độ kết cấu (u, v) từng đỉnh. */
  readonly uv: Float32Array;
  readonly indices: Uint16Array;
  readonly positions: Float32Array;
  private readonly weights: Float32Array; // 7 trọng số / đỉnh: thân, đầu, 4 đoạn tay, khung tranh (cố định)
  private readonly jawField: Float32Array; // hệ số hạ hàm / đỉnh
  private readonly lipField: Float32Array; // hệ số giãn môi / đỉnh
  readonly faceH: number;
  readonly lipY: number;
  readonly mouthCenter: Point;
  readonly mouthHalfW: number;
  readonly upperLip: Point;
  readonly lowerLip: Point;
  readonly joints: HeroRigData['joints'];
  private readonly jawDrop: number;

  /**
   * ``pinFrame``: ảnh gốc là bức tranh đục toàn khung (vd. tranh cuộn Trần Hưng Đạo) → mép tranh gắn vào xương
   * "khung" bất động, chỉ nhân vật bên trong chuyển động; khung tranh không bao giờ bị nghiêng/méo.
   */
  constructor(rig: HeroRigData, pinFrame = false, columns = 28, rows = 36) {
    this.width = rig.width;
    this.height = rig.height;
    this.columns = columns;
    this.rows = rows;
    this.joints = rig.joints;
    const j = rig.joints;
    const [fx0, fy0, fx1, fy1] = rig.face_box;
    const fw = Math.max(1, fx1 - fx0);
    this.faceH = Math.max(1, fy1 - fy0);
    const fh = this.faceH;
    this.upperLip = j.upper_lip;
    this.lowerLip = j.lower_lip;
    this.lipY = (j.upper_lip[1] + j.lower_lip[1]) / 2;
    this.mouthCenter = [(j.mouth_l[0] + j.mouth_r[0]) / 2, this.lipY];
    this.mouthHalfW = Math.max(3, (j.mouth_r[0] - j.mouth_l[0]) / 2, (rig.mouth_box[2] - rig.mouth_box[0]) * 0.22);
    this.jawDrop = 0.11 * fh;
    const chinY = j.chin[1];
    const neckY = Math.max(j.neck[1], chinY + 1);
    const headCenter: Point = [(fx0 + fx1) / 2, (j.head_top[1] + chinY) / 2];
    const headRx = 0.62 * fw;
    const headRy = Math.max(0.55 * fh, (chinY - j.head_top[1]) / 2 + 0.05 * fh);
    const hands: Point[] = [
      [j.wrist_l[0] + 0.35 * (j.wrist_l[0] - j.elbow_l[0]), j.wrist_l[1] + 0.35 * (j.wrist_l[1] - j.elbow_l[1])],
      [j.wrist_r[0] + 0.35 * (j.wrist_r[0] - j.elbow_r[0]), j.wrist_r[1] + 0.35 * (j.wrist_r[1] - j.elbow_r[1])],
    ];
    const vertexCount = (columns + 1) * (rows + 1);
    this.rest = new Float32Array(vertexCount * 2);
    this.uv = new Float32Array(vertexCount * 2);
    this.positions = new Float32Array(vertexCount * 2);
    this.weights = new Float32Array(vertexCount * 7);
    const margin = 0.07 * Math.min(rig.width, rig.height);
    this.jawField = new Float32Array(vertexCount);
    this.lipField = new Float32Array(vertexCount);
    for (let r = 0; r <= rows; r += 1) {
      for (let c = 0; c <= columns; c += 1) {
        const i = r * (columns + 1) + c;
        const x = (c / columns) * this.width;
        const y = (r / rows) * this.height;
        this.rest[i * 2] = x;
        this.rest[i * 2 + 1] = y;
        this.uv[i * 2] = c / columns;
        this.uv[i * 2 + 1] = r / rows;
        // Đầu: elip quanh mặt, tắt dần từ cằm xuống cổ.
        let head = falloff(Math.hypot((x - headCenter[0]) / headRx, (y - headCenter[1]) / headRy), 1, 1.35);
        if (head > 0) head *= 1 - smooth01((y - chinY) / Math.max(1, neckY + 0.25 * fh - chinY));
        const arms: number[] = [];
        const sides: Array<[number, Point, Point, Point]> = [
          [-1, j.shoulder_l, j.elbow_l, hands[0]],
          [1, j.shoulder_r, j.elbow_r, hands[1]],
        ];
        for (const [side, shoulder, elbow, hand] of sides) {
          let upper = falloff(segDist(x, y, shoulder, elbow), 0.3 * fw, 0.58 * fw);
          if (upper > 0) {
            upper *= smooth01((y - (shoulder[1] - 0.6 * fh)) / (0.3 * fh));
            upper *= smooth01(((x - shoulder[0]) * side + 0.9 * fw) / (0.5 * fw));
          }
          const lower = falloff(segDist(x, y, elbow, hand), 0.24 * fw, 0.44 * fw);
          arms.push(upper * (1 - lower), lower);
        }
        // Vùng đầu được ưu tiên tuyệt đối: khuôn mặt không bao giờ bị kéo theo cánh tay.
        const armTotal = arms.reduce((a, b) => a + b, 0);
        const armScale = (armTotal > 1 ? 1 / armTotal : 1) * (1 - head);
        for (let k = 0; k < 4; k += 1) arms[k] *= armScale;
        const bodyW = Math.max(0, 1 - head - arms.reduce((a, b) => a + b, 0));
        // Độ "dính khung": 1 ở mép tranh, giảm dần về 0 sau ``margin`` pixel.
        const edge = Math.min(x, this.width - x, y, this.height - y);
        const pin = pinFrame ? 1 - smooth01(edge / margin) : 0;
        const keep = 1 - pin;
        this.weights.set([bodyW * keep, head * keep, arms[0] * keep, arms[1] * keep, arms[2] * keep, arms[3] * keep, pin], i * 7);
        // Hàm dưới: chuông ngang quanh miệng × hồ sơ dọc dưới đường môi.
        const horizontal = falloff(Math.abs(x - this.mouthCenter[0]), 0.55 * this.mouthHalfW, 1.6 * this.mouthHalfW);
        let vertical = 0;
        if (y > this.lipY) {
          vertical = smooth01((y - this.lipY) / (0.1 * fh)) * (1 - smooth01((y - (chinY + 0.05 * fh)) / (0.55 * fh)));
        }
        this.jawField[i] = horizontal * vertical;
        this.lipField[i] = horizontal * falloff(Math.abs(y - this.lipY), 0.04 * fh, 0.18 * fh);
      }
    }
    const indices: number[] = [];
    for (let r = 0; r < rows; r += 1) {
      for (let c = 0; c < columns; c += 1) {
        const a = r * (columns + 1) + c;
        const b = a + 1;
        const d = a + columns + 1;
        const e = d + 1;
        indices.push(a, d, b, b, d, e);
      }
    }
    this.indices = new Uint16Array(indices);
    this.positions.set(this.rest);
  }

  private matrices(p: RigPose): Affine[] {
    const j = this.joints;
    const fh = this.faceH;
    const body = mul(
      translate(p.bodyDx * fh, p.bodyDy * fh),
      about(j.pelvis, mul(rotate(p.lean), [1 + p.breath, 0, 0, 0, 1 + p.breath, 0])),
    );
    const head = mul(body, about(j.neck, mul(translate(0, p.headNod * fh), rotate(p.headTilt))));
    const upperL = mul(body, about(j.shoulder_l, rotate(p.upperL)));
    const lowerL = mul(upperL, about(j.elbow_l, rotate(p.lowerL)));
    const upperR = mul(body, about(j.shoulder_r, rotate(p.upperR)));
    const lowerR = mul(upperR, about(j.elbow_r, rotate(p.lowerR)));
    return [body, head, upperL, lowerL, upperR, lowerR, [1, 0, 0, 0, 1, 0]];
  }

  /** Cập nhật ``positions`` cho tư thế ``p`` (LBS thuận). Tư thế nghỉ → đúng toạ độ gốc. */
  deform(p: RigPose): Float32Array {
    const mats = this.matrices(p);
    const drop = p.jawOpen * this.jawDrop;
    const widen = p.mouthWidth - 1;
    const count = this.rest.length / 2;
    for (let i = 0; i < count; i += 1) {
      const x0 = this.rest[i * 2];
      const y0 = this.rest[i * 2 + 1];
      const x = x0 + widen * (x0 - this.mouthCenter[0]) * this.lipField[i];
      const y = y0 + drop * this.jawField[i];
      let ox = 0;
      let oy = 0;
      for (let b = 0; b < 7; b += 1) {
        const w = this.weights[i * 7 + b];
        if (w === 0) continue;
        const m = mats[b];
        ox += w * (m[0] * x + m[1] * y + m[2]);
        oy += w * (m[3] * x + m[4] * y + m[5]);
      }
      this.positions[i * 2] = ox;
      this.positions[i * 2 + 1] = oy;
    }
    return this.positions;
  }

  /** Vị trí trên ảnh của điểm nghỉ (dùng đặt khoang miệng). */
  forwardPoint(p: RigPose, point: Point, jawWeight: number): Point {
    const mats = this.matrices(p);
    return apply(mats[1], point[0], point[1] + p.jawOpen * this.jawDrop * jawWeight);
  }

  mouthCavity(p: RigPose): { cx: number; top: number; bottom: number; halfW: number } | null {
    const restGap = Math.max(1, this.lowerLip[1] - this.upperLip[1]);
    const upper = this.forwardPoint(p, this.upperLip, 0);
    const lower = this.forwardPoint(p, this.lowerLip, 0.55);
    const gap = lower[1] - upper[1] - restGap;
    if (p.jawOpen < 0.02 || gap < 0.8) return null;
    const top = upper[1] + 0.25 * restGap;
    return {
      cx: this.forwardPoint(p, this.mouthCenter, 0)[0],
      top,
      bottom: top + gap * 0.92,
      halfW: this.mouthHalfW * (0.55 + 0.35 * p.jawOpen) * p.mouthWidth,
    };
  }
}
