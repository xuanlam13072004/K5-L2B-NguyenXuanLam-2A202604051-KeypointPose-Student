#!/usr/bin/env python3
"""xem_json.py — mở file zip xuất từ CVAT và in (x, y, v) của từng điểm để kiểm tay (báo cáo mục 5).

  python xem_json.py src/exports/NguyenXuanLam_persons.zip 000000030960 2
  python xem_json.py src/exports/NguyenXuanLam_persons.zip 000000043719 1
  python xem_json.py src/exports/NguyenXuanLam_faces.zip   face_405456  1

Tham số: <file zip> <một phần tên ảnh> [người thứ mấy trong ảnh, mặc định 1]
"""
import json
import sys
import zipfile

if len(sys.argv) < 3:
    sys.exit(__doc__)
path, img_key = sys.argv[1], sys.argv[2]
nth = int(sys.argv[3]) if len(sys.argv) > 3 else 1

with zipfile.ZipFile(path) as z:
    name = next(n for n in z.namelist() if n.endswith(".json"))
    d = json.loads(z.read(name))

img = next((i for i in d["images"] if img_key in i["file_name"]), None)
if not img:
    sys.exit(f"Không thấy ảnh chứa '{img_key}' trong {path}")
anns = [a for a in d["annotations"] if a["image_id"] == img["id"]]
anns.sort(key=lambda a: a["id"])
if nth > len(anns):
    sys.exit(f"Ảnh {img['file_name']} chỉ có {len(anns)} skeleton")
a = anns[nth - 1]
cat = next(c for c in d["categories"] if c["id"] == a["category_id"])
kp = a["keypoints"]

print(f"File: {name}   Ảnh: {img['file_name']} ({img['width']}x{img['height']})   người #{nth}/{len(anns)}")
print(f"Label: {cat['name']}   annotation id: {a['id']}")
print(f"Số phần tử keypoints: {len(kp)}  (= {len(cat['keypoints'])} điểm x 3)")
print(f"{'điểm':18s} {'x':>8s} {'y':>8s}  v")
for i, n in enumerate(cat["keypoints"]):
    x, y, v = kp[3 * i: 3 * i + 3]
    tag = {0: "outside", 1: "occluded", 2: "thấy"}.get(v, "?")
    print(f"{n:18s} {x:8.1f} {y:8.1f}  {v}  ({tag})")
cnt = sum(1 for i in range(len(cat["keypoints"])) if kp[3 * i + 2] > 0)
vs = [kp[3 * i + 2] for i in range(len(cat["keypoints"]))]
print(f"Số điểm v=2: {vs.count(2)} · v=1: {vs.count(1)} · v=0: {vs.count(0)}")
print(f"num_keypoints trong file: {a['num_keypoints']}   đếm lại số điểm v>0: {cnt}   -> {'KHỚP' if cnt == a['num_keypoints'] else 'KHÔNG KHỚP'}")