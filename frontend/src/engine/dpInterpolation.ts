/**
 * DP Frame Interpolation (port của DPFrameInterpolator — Phase 2).
 *
 * Chia Q lượng tử khung hình cho các đoạn (giữ tư thế / chuyển tư thế):
 *   DP[s][b] = min_n DP[s−1][b−n] + cost_s(n)
 *   cost(chuyển i→j, n) = d(P_i, P_j)² / T(n)        (năng lượng chuyển động — tốc độ đều)
 *   cost(giữ i, n)       = λ·(T(n) − hold_i)²          (giữ tư thế đúng nhịp)
 * rồi nội suy Hermite bậc 3 (tiếp tuyến Catmull–Rom, = 0 tại tư thế có giữ) → chuyển động C¹ ở 60 FPS,
 * vòng lặp liền mạch (khung cuối nối khung đầu).
 */

export const TARGET_FPS = 60;

export interface KeyPose {
  values: number[];
  holdS: number;
}

interface Segment {
  kind: 'hold' | 'move';
  from: number;
  to: number;
  frames: number;
}

export interface InterpolatorOptions {
  fps?: number;
  quantumFrames?: number;
  minMoveS?: number;
  maxMoveS?: number;
  maxHoldS?: number;
  holdWeight?: number;
  weights?: number[];
}

export class DPFrameInterpolator {
  private readonly fps: number;
  private readonly quantum: number;
  private readonly minMoveS: number;
  private readonly maxMoveS: number;
  private readonly maxHoldS: number;
  private readonly holdWeight: number;
  private readonly weights: number[] | null;

  constructor(options: InterpolatorOptions = {}) {
    this.fps = options.fps ?? TARGET_FPS;
    this.quantum = options.quantumFrames ?? 3;
    this.minMoveS = options.minMoveS ?? 0.2;
    this.maxMoveS = options.maxMoveS ?? 10;
    this.maxHoldS = options.maxHoldS ?? 6;
    this.holdWeight = options.holdWeight ?? 3;
    this.weights = options.weights ?? null;
  }

  distance(a: number[], b: number[]): number {
    let sum = 0;
    for (let i = 0; i < a.length; i += 1) {
      const w = this.weights ? this.weights[i] ?? 1 : 1;
      const d = w * (a[i] - b[i]);
      sum += d * d;
    }
    return Math.sqrt(sum);
  }

  private segmentCost(kind: 'hold' | 'move', from: KeyPose, to: KeyPose, quanta: number): number {
    const seconds = (quanta * this.quantum) / this.fps;
    if (kind === 'hold') return this.holdWeight * (seconds - from.holdS) ** 2;
    if (quanta <= 0) return Number.POSITIVE_INFINITY;
    const d = this.distance(from.values, to.values);
    return (d * d) / seconds;
  }

  /** Lập lịch tối ưu bằng quy hoạch động; trả về các đoạn với số khung của từng đoạn. */
  schedule(keys: KeyPose[], totalFrames: number, loop: boolean): Segment[] {
    if (keys.length === 1) return [{ kind: 'hold', from: 0, to: 0, frames: totalFrames }];
    const segments: Array<Omit<Segment, 'frames'>> = [];
    keys.forEach((_, index) => {
      segments.push({ kind: 'hold', from: index, to: index });
      if (loop || index < keys.length - 1) segments.push({ kind: 'move', from: index, to: (index + 1) % keys.length });
    });
    const budget = Math.floor(totalFrames / this.quantum);
    const remainder = totalFrames - budget * this.quantum;
    let minMove = Math.max(1, Math.round((this.minMoveS * this.fps) / this.quantum));
    const maxMove = Math.max(minMove, Math.round((this.maxMoveS * this.fps) / this.quantum));
    const maxHold = Math.max(0, Math.round((this.maxHoldS * this.fps) / this.quantum));
    const moves = segments.filter((s) => s.kind === 'move').length;
    if (moves * minMove > budget) minMove = 1;
    const inf = Number.POSITIVE_INFINITY;
    const table: number[][] = Array.from({ length: segments.length + 1 }, () => new Array(budget + 1).fill(inf));
    const choice: number[][] = Array.from({ length: segments.length + 1 }, () => new Array(budget + 1).fill(-1));
    table[0][0] = 0;
    segments.forEach((segment, s) => {
      const low = segment.kind === 'move' ? minMove : 0;
      const high = Math.min(segment.kind === 'move' ? maxMove : maxHold, budget);
      const costs: number[] = [];
      for (let n = low; n <= high; n += 1) costs[n] = this.segmentCost(segment.kind, keys[segment.from], keys[segment.to], n);
      for (let used = 0; used <= budget; used += 1) {
        const base = table[s][used];
        if (base === inf) continue;
        for (let n = low; n <= high && used + n <= budget; n += 1) {
          const candidate = base + costs[n];
          if (candidate < table[s + 1][used + n]) {
            table[s + 1][used + n] = candidate;
            choice[s + 1][used + n] = n;
          }
        }
      }
    });
    const counts = new Array(segments.length).fill(0);
    let used = budget;
    if (table[segments.length][budget] === inf) {
      // Không khả thi (quá ít khung): chia đều cho các đoạn chuyển.
      const perMove = Math.max(1, Math.floor(budget / Math.max(1, moves)));
      return segments.map((s) => ({ ...s, frames: s.kind === 'move' ? perMove * this.quantum : 0 }));
    }
    for (let s = segments.length; s > 0; s -= 1) {
      counts[s - 1] = choice[s][used];
      used -= choice[s][used];
    }
    const result: Segment[] = segments.map((s, i) => ({ ...s, frames: counts[i] * this.quantum }));
    for (let i = result.length - 1; i >= 0; i -= 1) {
      if (result[i].kind === 'move') {
        result[i].frames += remainder;
        break;
      }
    }
    return result;
  }

  /** Dựng toàn bộ chuỗi tư thế 60 FPS (Hermite C¹). */
  run(keys: KeyPose[], durationS: number, loop = true): number[][] {
    const total = Math.max(2, Math.round(durationS * this.fps));
    const segments = this.schedule(keys, total, loop);
    const arrival = new Map<number, number>();
    const holds = new Map<number, number>();
    let cursor = 0;
    for (const s of segments) {
      if (s.kind === 'hold') {
        arrival.set(s.from, cursor);
        holds.set(s.from, s.frames);
      }
      cursor += s.frames;
    }
    const dims = keys[0].values.length;
    const count = keys.length;
    const tangent = (index: number): number[] => {
      if ((holds.get(index) ?? 0) > 0 || (!loop && (index === 0 || index === count - 1))) return new Array(dims).fill(0);
      const prev = (index - 1 + count) % count;
      const next = (index + 1) % count;
      let tPrev = arrival.get(prev) ?? 0;
      let tNext = arrival.get(next) ?? 0;
      const tHere = arrival.get(index) ?? 0;
      if (tPrev > tHere) tPrev -= total;
      if (tNext < tHere) tNext += total;
      const span = Math.max(1, tNext - tPrev);
      return keys[prev].values.map((a, i) => (keys[next].values[i] - a) / span);
    };
    const tangents = keys.map((_, i) => tangent(i));
    const frames: number[][] = [];
    for (const s of segments) {
      const a = keys[s.from].values;
      if (s.kind === 'hold') {
        for (let f = 0; f < s.frames; f += 1) frames.push(a.slice());
        continue;
      }
      const b = keys[s.to].values;
      const n = s.frames;
      const m0 = tangents[s.from];
      const m1 = tangents[s.to];
      for (let f = 0; f < n; f += 1) {
        const t = f / n;
        const h00 = 2 * t ** 3 - 3 * t ** 2 + 1;
        const h10 = t ** 3 - 2 * t ** 2 + t;
        const h01 = -2 * t ** 3 + 3 * t ** 2;
        const h11 = t ** 3 - t ** 2;
        frames.push(a.map((va, i) => h00 * va + h10 * n * m0[i] + h01 * b[i] + h11 * n * m1[i]));
      }
    }
    if (!loop && frames.length) frames[frames.length - 1] = keys[keys.length - 1].values.slice();
    while (frames.length < total) frames.push(frames[frames.length - 1].slice());
    return frames.slice(0, total);
  }
}
