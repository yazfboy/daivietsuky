/**
 * TextToVisemeDPSync phía trình duyệt (port Phase 2) — khẩu hình khớp luồng chữ đang stream.
 *
 * 1. Chữ mới đến (phần nối thêm của câu trả lời) → âm tiết tiếng Việt (số đọc thành chữ) → viseme theo
 *    phụ âm đầu · vần · phụ âm cuối, xếp lên đồng hồ nói với nhịp thích nghi theo tốc độ chữ đến.
 * 2. Viterbi DP trên từng khung 60 FPS: trạng thái (viseme, số khung đã giữ); chi phí phát xạ = độ lệch hình
 *    miệng so với mục tiêu, chi phí chuyển = độ đổi hình miệng; mỗi viseme giữ ≥ MIN_HOLD khung → hết nháy.
 * 3. ``stop()`` (khi isStreaming = false): xoá lịch, miệng khép NGAY LẬP TỨC và đứng yên.
 */

export const VISEMES = ['REST', 'MBP', 'FV', 'U', 'O', 'E', 'A'] as const;
export type Viseme = (typeof VISEMES)[number];

/** (độ mở hàm 0..1, độ rộng môi) */
export const VISEME_SHAPES: Record<Viseme, [number, number]> = {
  REST: [0, 1],
  MBP: [0, 0.94],
  FV: [0.16, 1.02],
  U: [0.32, 0.74],
  O: [0.58, 0.82],
  E: [0.42, 1.1],
  A: [0.86, 1],
};

const TONE_MARKS = /[̣̀́̃̉]/g;
const ONSETS = ['ngh', 'ng', 'nh', 'ch', 'tr', 'th', 'ph', 'kh', 'gh', 'gi', 'qu', 'b', 'c', 'd', 'đ', 'g', 'h', 'k', 'l', 'm', 'n', 'p', 'r', 's', 't', 'v', 'x'];
const CODAS = ['ng', 'nh', 'ch', 'c', 'm', 'n', 'p', 't'];
const VOWEL_VISEME: Record<string, Viseme> = {
  a: 'A', ă: 'A', â: 'A', e: 'E', ê: 'E', i: 'E', y: 'E', o: 'O', ô: 'O', ơ: 'E', u: 'U', ư: 'E',
};
const ONSET_VISEME: Record<string, Viseme> = { b: 'MBP', m: 'MBP', p: 'MBP', ph: 'FV', v: 'FV', qu: 'U' };
const CODA_VISEME: Record<string, Viseme> = { m: 'MBP', p: 'MBP', t: 'REST', c: 'REST', ch: 'REST' };
const DIGITS = ['không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín'];
const SCALES = ['', 'nghìn', 'triệu', 'tỷ'];
const TOKEN_RE = /\p{N}+(?:[.,]\p{N}+)*|\p{L}+/gu;

const hasVowel = (text: string) => [...text].some((ch) => ch in VOWEL_VISEME);

function groupWords(group: number, full: boolean): string[] {
  const h = Math.floor(group / 100);
  const t = Math.floor(group / 10) % 10;
  const u = group % 10;
  const words: string[] = [];
  if (h || full) words.push(DIGITS[h], 'trăm');
  if (t === 0) {
    if (u) words.push(...(h || full ? ['linh', DIGITS[u]] : [DIGITS[u]]));
  } else if (t === 1) {
    words.push('mười');
    if (u) words.push(u === 5 ? 'lăm' : DIGITS[u]);
  } else {
    words.push(DIGITS[t], 'mươi');
    if (u) words.push(({ 1: 'mốt', 4: 'tư', 5: 'lăm' } as Record<number, string>)[u] ?? DIGITS[u]);
  }
  return words;
}

/** Đọc số thành chữ tiếng Việt (1288 → một nghìn hai trăm tám mươi tám). */
export function numberWords(token: string): string[] {
  const digits = token.replace(/\D/g, '');
  if (!digits) return [];
  if (digits.length > 12) return [...digits].map((d) => DIGITS[Number(d)]);
  let value = Number(digits);
  if (value === 0) return ['không'];
  const groups: number[] = [];
  while (value) {
    groups.push(value % 1000);
    value = Math.floor(value / 1000);
  }
  const words: string[] = [];
  for (let pos = groups.length - 1; pos >= 0; pos -= 1) {
    if (groups[pos] === 0) continue;
    words.push(...groupWords(groups[pos], pos !== groups.length - 1));
    if (pos > 0) words.push(SCALES[pos]);
  }
  return words;
}

export function textSyllables(text: string): string[] {
  const out: string[] = [];
  for (const token of text.normalize('NFC').match(TOKEN_RE) ?? []) {
    if (/^\p{N}/u.test(token)) out.push(...numberWords(token));
    else out.push(token);
  }
  return out;
}

/** Một âm tiết → chuỗi (viseme, tỉ lệ thời lượng). */
export function syllableVisemes(syllable: string): Array<[Viseme, number]> {
  const text = [...syllable.toLowerCase().normalize('NFD').replace(TONE_MARKS, '').normalize('NFC')]
    .filter((ch) => /\p{L}/u.test(ch))
    .join('');
  if (!text) return [];
  let onset = '';
  for (const candidate of ONSETS) {
    if (text.startsWith(candidate)) {
      const rest = text.slice(candidate.length);
      if ((candidate === 'gi' || candidate === 'qu') && !hasVowel(rest)) continue;
      onset = candidate;
      break;
    }
  }
  const rest = text.slice(onset.length);
  let coda = '';
  for (const candidate of CODAS) {
    if (rest.endsWith(candidate) && hasVowel(rest.slice(0, rest.length - candidate.length))) {
      coda = candidate;
      break;
    }
  }
  const nucleus = coda ? rest.slice(0, rest.length - coda.length) : rest;
  const vowels: Viseme[] = [];
  for (const ch of nucleus) {
    const v = VOWEL_VISEME[ch];
    if (v && vowels[vowels.length - 1] !== v) vowels.push(v);
  }
  if (!vowels.length) vowels.push('E');
  const onsetV = ONSET_VISEME[onset];
  const codaV = CODA_VISEME[coda];
  const onsetShare = onsetV ? 0.22 : 0;
  const codaShare = codaV ? 0.18 : 0;
  let main = 0;
  vowels.forEach((v, i) => {
    if (VISEME_SHAPES[v][0] > VISEME_SHAPES[vowels[main]][0]) main = i;
  });
  const raw = vowels.map((_, i) => (i === main ? 2 : 1));
  const scale = (1 - onsetShare - codaShare) / raw.reduce((a, b) => a + b, 0);
  const units: Array<[Viseme, number]> = [];
  if (onsetV) units.push([onsetV, onsetShare]);
  vowels.forEach((v, i) => units.push([v, raw[i] * scale]));
  if (codaV) units.push([codaV, codaShare]);
  return units;
}

const MIN_HOLD = 3;
const SWITCH_WEIGHT = 0.6;
const FRAME_MS = 1000 / 60;
const INDEX: Record<Viseme, number> = Object.fromEntries(VISEMES.map((v, i) => [v, i])) as Record<Viseme, number>;

function emissionCost(target: Viseme, candidate: Viseme): number {
  if (target === candidate) return 0;
  const [to, tw] = VISEME_SHAPES[target];
  const [co, cw] = VISEME_SHAPES[candidate];
  let cost = 0.25 + 1.2 * Math.abs(to - co) + 0.8 * Math.abs(tw - cw);
  if (target === 'REST' && co > 0.05) cost += 0.5;
  if (target !== 'REST' && candidate === 'REST') cost += 0.3;
  return cost;
}

const EMISSION = VISEMES.map((t) => VISEMES.map((c) => emissionCost(t, c)));
const SWITCH = VISEMES.map((a) =>
  VISEMES.map((b) => (a === b ? 0 : SWITCH_WEIGHT * (0.5 + Math.abs(VISEME_SHAPES[a][0] - VISEME_SHAPES[b][0])))),
);

/** Viterbi trên trạng thái (viseme, số khung đã giữ ≤ MIN_HOLD); bắt đầu và kết thúc ở REST. */
export function viterbiVisemes(targets: Viseme[]): Viseme[] {
  const V = VISEMES.length;
  const H = MIN_HOLD;
  const W = H + 1;
  const inf = Number.POSITIVE_INFINITY;
  const rest = INDEX.REST;
  let current = Array.from({ length: V }, () => new Array<number>(W).fill(inf));
  current[rest][H] = EMISSION[INDEX[targets[0]]][rest];
  const back: Int32Array[] = [];
  for (let t = 1; t < targets.length; t += 1) {
    const emission = EMISSION[INDEX[targets[t]]];
    const next = Array.from({ length: V }, () => new Array<number>(W).fill(inf));
    const pointer = new Int32Array(V * W).fill(-1);
    for (let v = 0; v < V; v += 1) {
      for (let k = 1; k < W; k += 1) {
        const prev = current[v][k];
        if (prev === inf) continue;
        const nk = k < H ? k + 1 : H;
        if (prev + emission[v] < next[v][nk]) {
          next[v][nk] = prev + emission[v];
          pointer[v * W + nk] = v * W + k;
        }
      }
      let best = inf;
      let source = -1;
      for (let u = 0; u < V; u += 1) {
        if (u !== v && current[u][H] < inf && current[u][H] + SWITCH[u][v] < best) {
          best = current[u][H] + SWITCH[u][v];
          source = u * W + H;
        }
      }
      if (best + emission[v] < next[v][1]) {
        next[v][1] = best + emission[v];
        pointer[v * W + 1] = source;
      }
    }
    back.push(pointer);
    current = next;
  }
  let endK = 1;
  for (let k = 2; k < W; k += 1) if (current[rest][k] < current[rest][endK]) endK = k;
  let state = rest * W + endK;
  if (current[rest][endK] === inf) {
    let best = inf;
    for (let v = 0; v < V; v += 1) {
      for (let k = 1; k < W; k += 1) {
        if (current[v][k] < best) {
          best = current[v][k];
          state = v * W + k;
        }
      }
    }
  }
  const path = new Array<number>(targets.length);
  for (let t = targets.length - 1; t > 0; t -= 1) {
    path[t] = Math.floor(state / W);
    state = back[t - 1][state];
  }
  path[0] = Math.floor(state / W);
  return path.map((i) => VISEMES[i]);
}

interface Interval {
  start: number;
  end: number;
  viseme: Viseme;
}

/** Bộ đồng bộ khẩu hình cho luồng chữ: push(chữ mới) · stop() · sample(thời điểm) → độ mở, độ rộng. */
/** Đoạn khẩu hình do backend gửi kèm cụm chữ (ms tương đối trong cụm). */
export interface VisemeHint {
  viseme: string;
  start_ms: number;
  end_ms: number;
}

export class StreamingMouthSync {
  private intervals: Interval[] = [];
  private planStart = 0;
  private plan: Viseme[] = [];
  private queueEnd = 0;
  private lastPush = 0;
  private rateEma = 0;
  private openness = 0;
  private width = 1;
  private lastSample = 0;
  private stopped = true;

  /** Thêm phần chữ vừa stream tới; lên lịch âm tiết và chạy lại Viterbi cho phần còn lại. */
  push(newText: string, now: number, hint?: ReadonlyArray<VisemeHint>): void {
    const syllables = textSyllables(newText);
    if (!syllables.length) return;
    this.stopped = false;
    if (this.lastPush > 0) {
      const gap = Math.max(16, now - this.lastPush);
      const rate = gap / syllables.length;
      this.rateEma = this.rateEma ? 0.7 * this.rateEma + 0.3 * rate : rate;
    }
    this.lastPush = now;
    const backlog = Math.max(0, this.queueEnd - now);
    // Nhịp nói thích nghi: theo tốc độ chữ đến, nhưng đủ chậm để khẩu hình nhìn rõ, nhanh hơn khi tồn đọng.
    let perSyllable = Math.min(230, Math.max(95, this.rateEma || 180));
    if (backlog > 900) perSyllable = 66; // ≈ 4 khung/âm tiết — vẫn ≥ MIN_HOLD, bám kịp luồng chữ nhanh
    let clock = Math.max(now, this.queueEnd);
    const isPunctPause = /[.!?…]\s*$/.test(newText.trim()) ? 260 : /[,;:]\s*$/.test(newText.trim()) ? 120 : 0;
    const usable = (hint ?? []).filter(
      (seg) => (VISEMES as readonly string[]).includes(seg.viseme) && seg.end_ms > seg.start_ms,
    );
    const span = usable.length ? usable[usable.length - 1].end_ms - usable[0].start_ms : 0;
    if (span > 0) {
      // Khẩu hình Viterbi DP của backend cho đúng cụm chữ này, co giãn theo nhịp chữ đang stream tới.
      const scale = (syllables.length * perSyllable) / span;
      const base = usable[0].start_ms;
      for (const seg of usable) {
        this.intervals.push({
          start: clock + (seg.start_ms - base) * scale,
          end: clock + (seg.end_ms - base) * scale,
          viseme: seg.viseme as Viseme,
        });
      }
      clock += syllables.length * perSyllable;
    } else {
      for (const syllable of syllables) {
        const units = syllableVisemes(syllable);
        let cursor = clock;
        for (const [viseme, share] of units.length ? units : ([['E', 1]] as Array<[Viseme, number]>)) {
          const end = cursor + share * perSyllable;
          this.intervals.push({ start: cursor, end, viseme });
          cursor = end;
        }
        clock += perSyllable;
      }
    }
    this.queueEnd = clock + Math.min(isPunctPause, perSyllable * 1.5);
    this.intervals = this.intervals.filter((iv) => iv.end > now - 200);
    this.replan(now);
  }

  private replan(now: number): void {
    const end = this.queueEnd + 200;
    const frames = Math.max(2, Math.ceil((end - now) / FRAME_MS));
    const targets: Viseme[] = new Array(frames).fill('REST');
    let cursor = 0;
    for (let f = 0; f < frames; f += 1) {
      const t = now + (f + 0.5) * FRAME_MS;
      while (cursor < this.intervals.length && this.intervals[cursor].end <= t) cursor += 1;
      const iv = this.intervals[cursor];
      if (iv && iv.start <= t) targets[f] = iv.viseme;
    }
    targets[0] = 'REST';
    this.planStart = now;
    this.plan = viterbiVisemes(targets);
  }

  /** isStreaming = false → miệng khép ngay lập tức và dừng cử động. */
  stop(): void {
    this.intervals = [];
    this.plan = [];
    this.queueEnd = 0;
    this.lastPush = 0;
    this.rateEma = 0;
    this.openness = 0;
    this.width = 1;
    this.stopped = true;
  }

  get active(): boolean {
    return !this.stopped;
  }

  sample(now: number): { open: number; width: number; viseme: Viseme } {
    if (this.stopped) return { open: 0, width: 1, viseme: 'REST' };
    const index = Math.floor((now - this.planStart) / FRAME_MS);
    const viseme: Viseme = index >= 0 && index < this.plan.length ? this.plan[index] : 'REST';
    const [targetOpen, targetWidth] = VISEME_SHAPES[viseme];
    const dt = Math.min(100, Math.max(0, now - this.lastSample || FRAME_MS));
    this.lastSample = now;
    const rate = 1 - Math.exp(-dt / (targetOpen > this.openness ? 30 : 45));
    this.openness += (targetOpen - this.openness) * rate;
    this.width += (targetWidth - this.width) * rate;
    if (viseme === 'REST' && now > this.queueEnd + 150) {
      this.openness = 0;
      this.width = 1;
    }
    return { open: this.openness, width: this.width, viseme };
  }
}
