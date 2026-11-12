#!/usr/bin/env python3
"""model_face.py — chạy MediaPipe Face Landmarker trên máy bạn, chọn 13 điểm của lab, đẩy lên CVAT.

Cài một lần (Windows / macOS / Ubuntu, Python 3.9–3.12):
    pip install mediapipe

Chạy từ thư mục gốc repo (cạnh lab.py), sau khi đã chạy `python lab.py setup ...`:
    python model_face.py                     tạo task RIÊNG faces-model-<ten>, upload 8 ảnh mặt, gán 13 điểm
    python model_face.py --rerun             xóa kết quả cũ trong task đích rồi chạy lại
    python model_face.py --into-graded       ghi THẲNG vào task chấm điểm faces-<ten> (hỏi xác nhận)
    python model_face.py --preview           chỉ in tọa độ ra màn hình, không đụng CVAT

MediaPipe trả 478 điểm đánh số theo GIẢI PHẪU (mắt trái của người luôn là cùng số dù mặt quay
hướng nào), nên trái/phải theo người được giữ đúng. Script KHÔNG biết:
  - điểm bị sống mũi / tóc che (mặt nghiêng, tóc che) → bạn tự bấm Q hoặc O;
  - kính râm: script đoán bằng độ tối vùng mắt và đặt occluded, nhưng vẫn phải xem lại.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import lab  # noqa: E402
from lab import ok, warn, step, die  # noqa: E402

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
MODEL_FILE = lab.DATA / "face_landmarker.task"

# 13 điểm của lab  →  chỉ số MediaPipe Face Mesh (trái/phải theo NGƯỜI trong ảnh)
FACE13 = [
    ("left_eye_outer", 263), ("left_eye_inner", 362), ("left_eye_upper", 386), ("left_eye_lower", 374),
    ("right_eye_outer", 33), ("right_eye_inner", 133), ("right_eye_upper", 159), ("right_eye_lower", 145),
    ("nose_tip", 1),
    ("mouth_left", 291), ("mouth_right", 61),
    ("lip_upper", 0), ("lip_lower", 17),
]
EYES = {"left": [263, 362, 386, 374], "right": [33, 133, 159, 145]}
DARK_EYE = 25  # độ sáng trung bình vùng mắt (0–255) dưới ngưỡng này → coi là kính râm tối
               # (đo thật: kính râm 6–11; mắt trong bóng / sau tóc 35–47 → không tính là kính râm)


# ------------------------------------------------------------------------------------------ MediaPipe
def load_landmarker():
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
    except ImportError:
        die("Chưa cài mediapipe.", "Chạy: pip install mediapipe   (cần Python 3.9–3.12)")
    if not MODEL_FILE.exists():
        MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        print("  Đang tải model face_landmarker.task (~4 MB)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_FILE)
    opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(MODEL_FILE)),
                                        num_faces=3, min_face_detection_confidence=0.3)
    return mp, vision.FaceLandmarker.create_from_options(opts)


def eye_brightness(gray, pts):
    """Độ sáng trung bình trong khung bao 4 điểm mắt (mở rộng 20%)."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    x0, x1 = int(max(0, min(xs) - 0.2 * w)), int(min(gray.shape[1], max(xs) + 0.2 * w + 1))
    y0, y1 = int(max(0, min(ys) - 0.5 * h - 2)), int(min(gray.shape[0], max(ys) + 0.5 * h + 3))
    patch = gray[y0:y1, x0:x1]
    return float(patch.mean()) if patch.size else 255.0


def detect(mp, landmarker, path):
    """Trả dict {tên điểm: (x, y, outside, occluded)} cho khuôn mặt lớn nhất, hoặc None."""
    img = mp.Image.create_from_file(str(path))
    W, H = img.width, img.height
    res = landmarker.detect(img)
    if not res.face_landmarks:
        return None, {}
    def area(lms):
        xs, ys = [l.x for l in lms], [l.y for l in lms]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))
    lms = max(res.face_landmarks, key=area)            # ảnh có người thứ hai bị cắt → lấy mặt lớn nhất
    px = {i: (lms[i].x * W, lms[i].y * H) for _, i in FACE13}
    arr = img.numpy_view()
    gray = arr[..., :3].mean(axis=2) if arr.ndim == 3 else arr
    info = {"faces": len(res.face_landmarks)}
    dark = {}
    for side, idx in EYES.items():
        b = eye_brightness(gray, [px[i] for i in idx])
        info[f"{side}_eye_brightness"] = round(b)
        dark[side] = b < DARK_EYE
    out = {}
    for name, i in FACE13:
        x, y = px[i]
        outside = not (0 <= x < W and 0 <= y < H)
        side = "left" if name.startswith("left_eye") else "right" if name.startswith("right_eye") else None
        out[name] = (x, y, outside, bool(side and dark[side]))
    return out, info


# ------------------------------------------------------------------------------------------ CVAT
def main():
    lab.setup_console()
    ap = argparse.ArgumentParser(description="MediaPipe → 13 điểm mặt của lab → CVAT")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--user")
    ap.add_argument("--task", help="tên task đích (mặc định faces-model-<ten>)")
    ap.add_argument("--into-graded", action="store_true", help="ghi vào task chấm điểm faces-<ten> (hỏi xác nhận)")
    ap.add_argument("--rerun", action="store_true", help="xóa nhãn đang có trong task đích rồi gán lại")
    ap.add_argument("--preview", action="store_true", help="chỉ chạy model và in kết quả, không đụng CVAT")
    ap.add_argument("--quality", type=int, default=75)
    args = ap.parse_args()

    if not lab.ASSIGN_FILE.exists():
        die("Chưa có src/exports/assignment.json.", "Chạy 'python lab.py setup --mssv ... --ten ...' trước.")
    rec = json.loads(lab.ASSIGN_FILE.read_text(encoding="utf-8"))
    ten = rec["ten"]
    files = lab.download([{"file": f} for f in rec["faces"]], lab.DATA / "faces")

    step("1. Chạy MediaPipe trên 8 ảnh mặt")
    mp, lm = load_landmarker()
    results = {}
    for f in files:
        pts, info = detect(mp, lm, f)
        results[f.name] = pts
        if pts is None:
            warn(f"{f.name}: không tìm thấy mặt → phải gán tay")
            continue
        flags = [n for n, (_, _, _, occ) in pts.items() if occ]
        extra = f", {info['faces']} mặt (lấy mặt lớn nhất)" if info["faces"] > 1 else ""
        note = f" → kính râm? 4 điểm mắt đặt occluded: {', '.join(sorted({n.split('_')[0] for n in flags}))}" if flags else ""
        ok(f"{f.name}: 13 điểm (độ sáng mắt T/P {info['left_eye_brightness']}/{info['right_eye_brightness']}{extra}){note}")
        if args.preview:
            for n, (x, y, o, c) in pts.items():
                print(f"      {n:16s} {x:7.1f} {y:7.1f}" + ("  outside" if o else "") + ("  occluded" if c else ""))
    if args.preview:
        return

    graded = f"faces-{ten}"
    target = graded if args.into_graded else (args.task or f"faces-model-{ten}")
    if target == graded:
        warn(f"Bạn sắp ghi điểm do MODEL sinh ra vào task CHẤM ĐIỂM {graded}.")
        print("  RULES.md cấm việc này trừ khi coach cho phép; lab.py export sẽ nộp đúng các điểm này.")
        if input("  Coach đã cho phép bằng văn bản? Gõ 'co' để tiếp tục: ").strip().lower() != "co":
            die("Đã hủy. Chạy không có --into-graded để dùng task riêng.")

    step(f"2. Đẩy lên CVAT: {target}")
    cv = lab.Cvat(args.url)
    cv.login(args.user)
    proj = cv.project(rec.get("project", lab.PROJECT))
    if not proj:
        die("Không thấy project d04-keypoint.", "Chạy 'python lab.py setup ...' trước.")
    labels = cv.all(f"/api/labels?project_id={proj['id']}")
    face = next((l for l in labels if l["name"] == "face" and not l.get("parent_id")), None)
    if not face:
        die("Project không có label 'face'.")
    # CVAT trả các điểm con lồng trong label cha (trường "sublabels"); bản cũ có thể trả riêng với parent_id
    sub = {s["name"]: s["id"] for s in face.get("sublabels") or []}
    if not sub:
        sub = {l["name"]: l["id"] for l in labels if l.get("parent_id") == face["id"]}
    if not sub:
        sub = {l["name"]: l["id"] for l in cv.all(f"/api/labels?parent_id={face['id']}")}
    missing = [n for n, _ in FACE13 if n not in sub]
    if missing:
        die("Label face thiếu điểm: " + ", ".join(missing), "Báo coach: label trong project sai.")

    t = cv.task(proj["id"], target)
    if not t:
        print(f"  Đang tạo {target} và tải {len(files)} ảnh lên...")
        t = cv.post("/api/tasks", {"name": target, "project_id": proj["id"]})
        cv.upload(t["id"], files, args.quality)
        ok(f"Đã tạo {target}")
    else:
        cur = cv.get(f"/api/tasks/{t['id']}/annotations")
        if cur.get("shapes") or cur.get("tracks"):
            if not args.rerun:
                die(f"{target} đã có {len(cur.get('shapes', []))} nhãn.",
                    "Thêm --rerun để xóa và gán lại (nhãn đang có trong task này sẽ mất).")
            cv.req("PUT", f"/api/tasks/{t['id']}/annotations", {"version": cur.get("version", 0), "tags": [], "shapes": [], "tracks": []})
            ok("Đã xóa nhãn cũ")
        ok(f"{target} đã có, dùng lại")

    frames = cv.get(f"/api/tasks/{t['id']}/data/meta")["frames"]
    shapes = []
    for fi, fr in enumerate(frames):
        pts = results.get(Path(fr["name"]).name)
        if not pts:
            continue
        shapes.append({
            "type": "skeleton", "frame": fi, "label_id": face["id"], "points": [], "source": "auto",
            "occluded": False, "outside": False, "attributes": [], "group": 0, "z_order": 0,
            "elements": [{
                "type": "points", "frame": fi, "label_id": sub[n], "points": [float(x), float(y)], "source": "auto",
                "occluded": occ, "outside": o, "attributes": [], "group": 0, "z_order": 0,
            } for n, (x, y, o, occ) in pts.items()],
        })
    cv.patch(f"/api/tasks/{t['id']}/annotations?action=create", {"version": 0, "tags": [], "shapes": shapes, "tracks": []})
    ok(f"Đã gán {len(shapes)} / {len(frames)} khuôn mặt")

    jobs = cv.all(f"/api/jobs?task_id={t['id']}")
    print("\nPHẢI XEM TAY (phóng to từng mắt):")
    print("  - Mặt nghiêng: mắt / khóe miệng phía xa bị sống mũi che → Q (đoán chắc) hoặc O (không đoán được).")
    print("  - Tóc che mắt: điểm sau tóc → Q hoặc O.")
    print("  - Kính râm: kiểm 4 điểm mắt đã là occluded (vòng rỗng). Kính trong: phải là v = 2.")
    print("  - Mí trên / mí dưới: model hay đặt lệch 1–3 px, kéo lại vào đúng mép mí.")
    if jobs:
        print(f"\nMở job: {args.url}/tasks/{t['id']}/jobs/{jobs[0]['id']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
