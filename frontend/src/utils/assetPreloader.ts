import { HERO_LIST } from '../data/heroes';
import {
  HeroId,
  HeroMetaData,
  HeroRigData,
  PreloadAssetStatus,
  PreloadProgress,
} from '../types/hero';

/**
 * Bộ nhớ đệm O(1) lưu trữ tài nguyên đã tải trước (Pre-loaded)
 * trong suốt thời gian chạy IntroMotion để chuyển đổi nhân vật tức thì (0ms delay).
 */
class HeroAssetMemoryCache {
  private statuses = new Map<HeroId, PreloadAssetStatus>();
  private bitmaps = new Map<HeroId, ImageBitmap>();
  private imageElements = new Map<HeroId, HTMLImageElement>();

  public setStatus(id: HeroId, status: PreloadAssetStatus): void {
    this.statuses.set(id, status);
  }

  public getStatus(id: HeroId): PreloadAssetStatus | undefined {
    return this.statuses.get(id);
  }

  public setBitmap(id: HeroId, bitmap: ImageBitmap): void {
    this.bitmaps.set(id, bitmap);
  }

  public getBitmap(id: HeroId): ImageBitmap | undefined {
    return this.bitmaps.get(id);
  }

  public setImageElement(id: HeroId, img: HTMLImageElement): void {
    this.imageElements.set(id, img);
  }

  public getImageElement(id: HeroId): HTMLImageElement | undefined {
    return this.imageElements.get(id);
  }

  public getAllStatuses(): Record<HeroId, PreloadAssetStatus> {
    const result = {} as Record<HeroId, PreloadAssetStatus>;
    for (const hero of HERO_LIST) {
      const existing = this.statuses.get(hero.id);
      result[hero.id] = existing ?? {
        characterId: hero.id,
        heroImageUrl: hero.heroImageUrl,
        loaded: false,
        decoded: false,
        width: 0,
        height: 0,
        elapsedMs: 0,
        rigData: null,
        metaData: null,
      };
    }
    return result;
  }
}

export const heroAssetCache = new HeroAssetMemoryCache();

/**
 * Tạo Web Worker inline chạy trên luồng riêng (Off-Main-Thread) để tải nhị phân ảnh
 * và giải mã trước bằng createImageBitmap (nếu trình duyệt hỗ trợ), kết hợp song song
 * với Image.decode() trên luồng hiển thị để làm nóng bộ nhớ đệm GPU/HTTP.
 */
function decodeImageInWorker(absoluteUrl: string): Promise<{ width: number; height: number } | null> {
  if (typeof Worker === 'undefined' || typeof Blob === 'undefined' || typeof URL === 'undefined') {
    return Promise.resolve(null);
  }

  const workerScript = `
    self.onmessage = async (event) => {
      const url = event.data;
      try {
        const response = await fetch(url, { cache: 'force-cache' });
        if (!response.ok) {
          self.postMessage({ ok: false });
          return;
        }
        const blob = await response.blob();
        if (typeof createImageBitmap === 'function') {
          const bitmap = await createImageBitmap(blob);
          const width = bitmap.width;
          const height = bitmap.height;
          bitmap.close();
          self.postMessage({ ok: true, width, height });
        } else {
          self.postMessage({ ok: true, width: 0, height: 0 });
        }
      } catch (err) {
        self.postMessage({ ok: false });
      }
    };
  `;

  return new Promise((resolve) => {
    let settled = false;
    let worker: Worker | null = null;
    let objectUrl: string | null = null;

    const cleanup = () => {
      if (worker) {
        worker.terminate();
        worker = null;
      }
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
    };

    try {
      const blob = new Blob([workerScript], { type: 'application/javascript' });
      objectUrl = URL.createObjectURL(blob);
      worker = new Worker(objectUrl);

      const timer = window.setTimeout(() => {
        if (!settled) {
          settled = true;
          cleanup();
          resolve(null);
        }
      }, 4500);

      worker.onmessage = (event: MessageEvent<{ ok: boolean; width?: number; height?: number }>) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        cleanup();
        if (event.data && event.data.ok) {
          resolve({
            width: event.data.width ?? 0,
            height: event.data.height ?? 0,
          });
        } else {
          resolve(null);
        }
      };

      worker.onerror = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        cleanup();
        resolve(null);
      };

      worker.postMessage(absoluteUrl);
    } catch {
      cleanup();
      resolve(null);
    }
  });
}

function decodeImageElement(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.fetchPriority = 'high';
    img.src = url;

    const finalize = () => {
      if (typeof img.decode === 'function') {
        img
          .decode()
          .then(() => resolve(img))
          .catch(() => resolve(img));
      } else {
        resolve(img);
      }
    };

    if (img.complete && img.naturalWidth > 0) {
      finalize();
      return;
    }

    img.onload = finalize;
    img.onerror = () => reject(new Error(`Không thể tải ảnh: ${url}`));
  });
}

async function fetchJsonSafe<T>(url: string): Promise<T | null> {
  try {
    const response = await fetch(url, { cache: 'force-cache' });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

/**
 * Thuật toán tải trước đa luồng (Multi-threaded Concurrent Preloader):
 * Khởi chạy đồng thời 3 luồng Worker + GPU Image.decode() + tải JSON Rig/Metadata
 * cho cả 3 vị anh hùng ngay khi IntroMotion bắt đầu hiển thị.
 */
export async function preloadAllHeroAssets(
  onProgress?: (progress: PreloadProgress) => void
): Promise<PreloadProgress> {
  const total = HERO_LIST.length;
  let completed = 0;

  const notify = () => {
    const items = heroAssetCache.getAllStatuses();
    const percentage = Math.round((completed / total) * 100);
    const snapshot: PreloadProgress = {
      completed,
      total,
      percentage,
      allReady: completed >= total,
      items,
    };
    onProgress?.(snapshot);
    return snapshot;
  };

  // Khởi tạo trạng thái ban đầu
  notify();

  const tasks = HERO_LIST.map(async (hero) => {
    const existing = heroAssetCache.getStatus(hero.id);
    if (existing && existing.loaded && existing.decoded) {
      completed += 1;
      notify();
      return existing;
    }

    const startTime = performance.now();
    const absoluteImageUrl = new URL(hero.heroImageUrl, window.location.origin).href;

    try {
      const [workerResult, imgElement, rigData, metaData] = await Promise.all([
        decodeImageInWorker(absoluteImageUrl),
        decodeImageElement(hero.heroImageUrl),
        fetchJsonSafe<HeroRigData>(hero.rigUrl),
        fetchJsonSafe<HeroMetaData>(hero.metaUrl),
      ]);

      heroAssetCache.setImageElement(hero.id, imgElement);

      if (typeof createImageBitmap === 'function') {
        try {
          const bitmap = await createImageBitmap(imgElement);
          heroAssetCache.setBitmap(hero.id, bitmap);
        } catch {
          // Nếu trình duyệt hạn chế tạo thêm bitmap, vẫn giữ HTMLImageElement đã decode
        }
      }

      const elapsedMs = Math.round(performance.now() - startTime);
      const status: PreloadAssetStatus = {
        characterId: hero.id,
        heroImageUrl: hero.heroImageUrl,
        loaded: true,
        decoded: true,
        width: imgElement.naturalWidth || workerResult?.width || 720,
        height: imgElement.naturalHeight || workerResult?.height || 960,
        elapsedMs,
        rigData,
        metaData,
      };

      heroAssetCache.setStatus(hero.id, status);
      completed += 1;
      notify();
      return status;
    } catch {
      const elapsedMs = Math.round(performance.now() - startTime);
      const fallbackStatus: PreloadAssetStatus = {
        characterId: hero.id,
        heroImageUrl: hero.heroImageUrl,
        loaded: false,
        decoded: false,
        width: 720,
        height: 960,
        elapsedMs,
        rigData: null,
        metaData: null,
      };
      heroAssetCache.setStatus(hero.id, fallbackStatus);
      completed += 1;
      notify();
      return fallbackStatus;
    }
  });

  await Promise.all(tasks);
  return notify();
}
