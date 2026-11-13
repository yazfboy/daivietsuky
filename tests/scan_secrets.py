"""Đại Việt Sử Ký — Quét lộ API key (chạy: python tests/scan_secrets.py).

* Đọc ``./.env`` LÚC CHẠY (không chép key vào code / test, không in key ra màn hình hay báo cáo).
* Với từng key: tìm bản đầy đủ VÀ mọi đoạn con dài ≥ 12 ký tự (mọi đoạn con ≥ 12 ký tự đều chứa một đoạn đúng
  12 ký tự, nên chỉ cần dò tập 12-gram của key).
* Phạm vi:
    1. mọi file đang được git theo dõi (``git ls-files``);
    2. toàn bộ lịch sử commit của MỌI nhánh, gồm nhánh ``gh-pages`` cũ — mọi blob trong ``git rev-list --all
       --objects`` và toàn văn ``git log -p --all``;
    3. ``frontend/dist/`` — sau khi chạy ``npm run build`` (cấu hình production) NGAY TRƯỚC khi quét.
* Chỉ in tên biến + ĐẠT / KHÔNG ĐẠT (kèm đường dẫn file / commit nếu lộ, không in nội dung).
  Nếu key từng lộ trong lịch sử: báo đổi key mới ngay — xóa file không xóa được lịch sử Git.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DIST = ROOT / "frontend" / "dist"
MIN_FRAGMENT = 12
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def read_secrets() -> list[tuple[str, str]]:
    """(tên, giá trị) của mọi bí mật trong .env. Dòng dạng ``TÊN=giá_trị`` lấy TÊN; dòng chỉ có key → KEY_n."""
    secrets: list[tuple[str, str]] = []
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = ""
        if "=" in line:
            name, line = (part.strip() for part in line.split("=", 1))
        value = line.strip().strip("'\"")
        if len(value) >= MIN_FRAGMENT:
            secrets.append((name or f"KEY_{len(secrets) + 1}", value))
    return secrets


def fragment_pattern(value: str) -> re.Pattern[bytes]:
    grams = sorted({value[i : i + MIN_FRAGMENT] for i in range(len(value) - MIN_FRAGMENT + 1)})
    return re.compile(b"|".join(re.escape(gram.encode("utf-8")) for gram in grams))


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def tracked_files() -> list[tuple[str, bytes]]:
    items = []
    # Gồm cả file mới chưa commit nhưng không bị .gitignore (sẽ lên Git ở commit kế tiếp).
    listing = git("ls-files", "-z") + b"\0" + git("ls-files", "-z", "--others", "--exclude-standard")
    for rel in listing.split(b"\0"):
        if rel:
            path = ROOT / rel.decode("utf-8", "surrogateescape")
            if path.is_file():
                items.append((path.relative_to(ROOT).as_posix(), path.read_bytes()))
    return items


def history_blobs() -> list[tuple[str, bytes]]:
    listing = git("rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines()
    names: dict[str, str] = {}
    for line in listing:
        oid, _, path = line.partition(" ")
        if path:
            names.setdefault(oid, path)
    if not names:
        return []
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, check=True, capture_output=True,
                          input="".join(f"{oid}\n" for oid in names).encode())
    data, pos, blobs = proc.stdout, 0, []
    while pos < len(data):
        end = data.index(b"\n", pos)
        header = data[pos:end].split()
        pos = end + 1
        if len(header) < 3:
            continue
        oid, kind, size = header[0].decode(), header[1], int(header[2])
        body = data[pos : pos + size]
        pos += size + 1
        if kind == b"blob":
            blobs.append((f"{names.get(oid, '?')} @ blob {oid[:10]}", body))
    log = git("log", "-p", "--all", "--text", "--no-color")
    blobs.append(("git log -p --all", log))
    return blobs


def dist_files() -> list[tuple[str, bytes]]:
    return [(p.relative_to(ROOT).as_posix(), p.read_bytes()) for p in sorted(DIST.rglob("*")) if p.is_file()]


def build_frontend() -> bool:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if npm is None:
        print("  KHÔNG ĐẠT — không tìm thấy npm để build production")
        return False
    env = {key: value for key, value in os.environ.items() if key != "VITE_BASE"}
    done = subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    print(f"  npm run build (production): {'ĐẠT' if done.returncode == 0 else 'KHÔNG ĐẠT'}")
    return done.returncode == 0


def scan(area: str, items: list[tuple[str, bytes]], secrets: list[tuple[str, re.Pattern[bytes]]]) -> bool:
    print(f"\n[{area}] {len(items)} mục")
    clean = True
    for name, pattern in secrets:
        hits = [label for label, body in items if pattern.search(body)]
        print(f"  {name}: {'ĐẠT' if not hits else 'KHÔNG ĐẠT'}")
        for label in hits[:10]:
            print(f"      lộ tại: {label}")
        clean &= not hits
    return clean


def main() -> int:
    if not ENV_PATH.is_file():
        print("KHÔNG ĐẠT — không có ./.env để đối chiếu")
        return 2
    raw = read_secrets()
    if not raw:
        print("KHÔNG ĐẠT — .env không có giá trị nào ≥ 12 ký tự")
        return 2
    secrets = [(name, fragment_pattern(value)) for name, value in raw]
    del raw
    print(f"Đối chiếu {len(secrets)} bí mật trong .env (bản đầy đủ + mọi đoạn con ≥ {MIN_FRAGMENT} ký tự).")
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT).returncode == 0
    tracked = bool(git("ls-files", ".env").strip())
    print(f"  .env nằm trong .gitignore: {'ĐẠT' if ignored else 'KHÔNG ĐẠT'}")
    print(f"  .env không được git theo dõi: {'ĐẠT' if not tracked else 'KHÔNG ĐẠT'}")
    ok = ignored and not tracked
    built = "--no-build" in sys.argv or build_frontend()
    ok &= built
    ok &= scan("1. File đang được git theo dõi", tracked_files(), secrets)
    history_ok = scan("2. Lịch sử commit mọi nhánh (gồm gh-pages)", history_blobs(), secrets)
    ok &= history_ok
    ok &= scan("3. frontend/dist/ (bản build production)", dist_files() if DIST.is_dir() else [], secrets)
    if not history_ok:
        print("\n!!! KEY ĐÃ TỪNG LỘ TRONG LỊCH SỬ GIT — hãy đổi key mới ngay. Xóa file không xóa được lịch sử.")
    print(f"\nKẾT QUẢ QUÉT KEY: {'ĐẠT' if ok else 'KHÔNG ĐẠT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
