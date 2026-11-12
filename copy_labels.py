#!/usr/bin/env python3
"""copy_labels.py — chép nhãn từ task model sang task nộp bài (cùng project, cùng ảnh).

  python copy_labels.py                persons-model-<ten> → persons-<ten>  và  faces-model-<ten> → faces-<ten>
  python copy_labels.py --only faces   chỉ chép phần mặt (hoặc --only persons)
  python copy_labels.py --replace      xóa nhãn đang có trong task nộp trước khi chép

Ghép ảnh theo TÊN FILE nên thứ tự ảnh trong hai task không cần giống nhau.
Giữ nguyên mọi chỉnh sửa tay (vị trí điểm, occluded, outside) bạn đã làm trong task model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import lab  # noqa: E402
from lab import ok, warn, step, die  # noqa: E402

DROP = ("id",)


def clean(shape, frame):
    s = {k: v for k, v in shape.items() if k not in DROP}
    s["frame"] = frame
    if s.get("elements"):
        s["elements"] = [dict({k: v for k, v in e.items() if k not in DROP}, frame=frame) for e in s["elements"]]
    return s


def copy(cv, proj, src_name, dst_name, replace):
    src, dst = cv.task(proj["id"], src_name), cv.task(proj["id"], dst_name)
    if not src:
        warn(f"Không thấy {src_name}, bỏ qua."); return
    if not dst:
        die(f"Không thấy {dst_name}.", "Chạy 'python lab.py setup ...' trước.")
    sf = [Path(f["name"]).name for f in cv.get(f"/api/tasks/{src['id']}/data/meta")["frames"]]
    df = {Path(f["name"]).name: i for i, f in enumerate(cv.get(f"/api/tasks/{dst['id']}/data/meta")["frames"])}
    ann = cv.get(f"/api/tasks/{src['id']}/annotations")
    cur = cv.get(f"/api/tasks/{dst['id']}/annotations")
    if cur.get("shapes") or cur.get("tracks"):
        if not replace:
            die(f"{dst_name} đang có {len(cur.get('shapes', []))} nhãn.",
                "Thêm --replace để xóa nhãn đó rồi chép (không lấy lại được).")
        cv.req("PUT", f"/api/tasks/{dst['id']}/annotations", {"version": cur.get("version", 0), "tags": [], "shapes": [], "tracks": []})
    shapes, skipped = [], 0
    for s in ann.get("shapes", []):
        name = sf[s["frame"]]
        if name not in df:
            skipped += 1; continue
        shapes.append(clean(s, df[name]))
    cv.patch(f"/api/tasks/{dst['id']}/annotations?action=create", {"version": 0, "tags": [], "shapes": shapes, "tracks": []})
    per = {}
    for s in shapes:
        per[s["frame"]] = per.get(s["frame"], 0) + 1
    ok(f"{src_name} → {dst_name}: {len(shapes)} skeleton trên {len(per)}/{len(df)} ảnh" + (f" (bỏ {skipped} không khớp ảnh)" if skipped else ""))
    empty = [n for n, i in df.items() if i not in per]
    if empty:
        warn("Ảnh chưa có skeleton nào: " + ", ".join(sorted(empty)))


def main():
    lab.setup_console()
    ap = argparse.ArgumentParser(description="Chép nhãn từ task model sang task nộp bài")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--user")
    ap.add_argument("--only", choices=["persons", "faces"])
    ap.add_argument("--replace", action="store_true", help="xóa nhãn đang có trong task nộp trước khi chép")
    args = ap.parse_args()
    if not lab.ASSIGN_FILE.exists():
        die("Chưa có src/exports/assignment.json.", "Chạy 'python lab.py setup ...' trước.")
    rec = json.loads(lab.ASSIGN_FILE.read_text(encoding="utf-8"))
    ten = rec["ten"]
    cv = lab.Cvat(args.url)
    cv.login(args.user)
    proj = cv.project(rec.get("project", lab.PROJECT))
    if not proj:
        die("Không thấy project d04-keypoint.")
    step("Chép nhãn")
    for kind in ("persons", "faces"):
        if args.only in (None, kind):
            copy(cv, proj, f"{kind}-model-{ten}", f"{kind}-{ten}", args.replace)
    print("\nMở job trong trang Jobs, tải lại trang (Ctrl+F5) để thấy nhãn mới.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")