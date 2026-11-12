#!/usr/bin/env python3
"""model_pose.py — chạy model pose HRNet (CVAT serverless) lên bộ ảnh người của bạn.

Yêu cầu: đã deploy function `pth-mmpose-hrnet32` lên Nuclio (tab Models của CVAT thấy
"Human pose estimation") và đã chạy `python lab.py setup ...` (có src/exports/assignment.json).

Chạy từ thư mục gốc repo, không cần cài thêm thư viện:

  python model_pose.py                       tạo task RIÊNG persons-model-<ten>, upload 12 ảnh, chạy model
  python model_pose.py --task persons-model-<ten> --rerun     chạy lại model (xóa kết quả model cũ)
  python model_pose.py --into-graded         ghi THẲNG vào task chấm điểm persons-<ten> (hỏi xác nhận)

Mặc định script KHÔNG đụng vào task persons-<ten> mà lab.py export dùng để nộp bài:
RULES.md của lab cấm nộp điểm do model sinh ra trừ khi coach cho phép.

Model trả mọi điểm ở v = 2 (trừ điểm confidence thấp → outside). Nó KHÔNG biết đặt occluded (v = 1),
không biết luật 60 px, poster / gương. Script hậu xử lý phần làm được bằng luật, phần còn lại phải xem tay.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import lab  # noqa: E402  (dùng lại client CVAT, đăng nhập, upload của lab.py)
from lab import ok, warn, bad, step, die  # noqa: E402

FUNCTION = "pth-mmpose-hrnet32"
COCO17 = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
          "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip",
          "left_knee", "right_knee", "left_ankle", "right_ankle"]
# HRNet (ubody / COCO-WholeBody) đặt tên 17 điểm thân là "1".."17" theo đúng thứ tự COCO
MAPPING = {"body": {"name": "person", "sublabels": {str(i + 1): {"name": n} for i, n in enumerate(COCO17)}}}


# ------------------------------------------------------------------------------------------ hậu xử lý
def postprocess(annotations, frames, sublabel_names, min_height=60, head_feet_pad=1.15):
    """Áp luật của lab lên kết quả model. Trả (annotations mới, nhật ký).

    - Điểm nằm ngoài khung ảnh mà vẫn v > 0 → outside (luật: không để v > 0 ngoài ảnh).
    - Skeleton có chiều cao ước lượng < min_height px → xóa (luật 60 px, ghi vào báo cáo thay vì gán).
      Chiều cao = bề dọc các điểm thấy được × head_feet_pad (keypoint không có đỉnh đầu / gót chân).
    - Skeleton không còn điểm nào thấy được → xóa.
    """
    log = {"removed_small": [], "removed_empty": [], "clipped": [], "kept": {}}
    kept = []
    for s in annotations.get("shapes", []):
        if s.get("type") != "skeleton":
            kept.append(s)
            continue
        fr = frames[s["frame"]]
        w, h = fr["width"], fr["height"]
        vis = []
        for el in s.get("elements", []):
            if el.get("outside"):
                continue
            x, y = el["points"][0], el["points"][1]
            if not (0 <= x < w and 0 <= y < h):
                el["outside"] = True
                log["clipped"].append((fr["name"], sublabel_names.get(el["label_id"], el["label_id"]), round(x), round(y)))
                continue
            vis.append((x, y))
        if not vis:
            log["removed_empty"].append(fr["name"])
            continue
        est_h = (max(y for _, y in vis) - min(y for _, y in vis)) * head_feet_pad
        if est_h < min_height:
            log["removed_small"].append((fr["name"], round(est_h)))
            continue
        kept.append(s)
        log["kept"][fr["name"]] = log["kept"].get(fr["name"], 0) + 1
    out = dict(annotations)
    out["shapes"] = kept
    return out, log


# ------------------------------------------------------------------------------------------ CVAT
def wait_lambda(cv, rid, timeout=3600):
    last = None
    for _ in range(timeout):
        st = cv.get(f"/api/lambda/requests/{rid}")
        status, prog = st.get("status"), st.get("progress")
        if (status, prog) != last:
            print(f"  model: {status} {prog if prog is not None else ''}%")
            last = (status, prog)
        if status == "finished":
            return
        if status == "failed":
            die(f"Model chạy lỗi: {st.get('exc_info') or st.get('message')}",
                "Xem log: nuctl get function --platform local; docker logs nuclio-nuclio-pth-mmpose-hrnet32")
        time.sleep(2)
    die("Model chạy quá lâu.")


def main():
    lab.setup_console()
    ap = argparse.ArgumentParser(description="Chạy HRNet pose của CVAT lên bộ ảnh người của bạn")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--user", help="tên đăng nhập CVAT (không có thì hỏi)")
    ap.add_argument("--task", help="tên task đích (mặc định persons-model-<ten>)")
    ap.add_argument("--into-graded", action="store_true", help="ghi vào task chấm điểm persons-<ten> (có hỏi xác nhận)")
    ap.add_argument("--rerun", action="store_true", help="xóa nhãn đang có trong task đích rồi chạy lại model")
    ap.add_argument("--threshold", type=float, default=0.55, help="confidence tối thiểu của một điểm (dưới → outside)")
    ap.add_argument("--min-height", type=int, default=60, help="người thấp hơn (px) thì xóa, theo luật lab")
    ap.add_argument("--quality", type=int, default=75)
    args = ap.parse_args()

    if not lab.ASSIGN_FILE.exists():
        die("Chưa có src/exports/assignment.json.", "Chạy 'python lab.py setup --mssv ... --ten ...' trước.")
    rec = json.loads(lab.ASSIGN_FILE.read_text(encoding="utf-8"))
    ten = rec["ten"]
    graded = f"persons-{ten}"
    target = graded if args.into_graded else (args.task or f"persons-model-{ten}")

    if target == graded:
        warn(f"Bạn sắp ghi điểm do MODEL sinh ra vào task CHẤM ĐIỂM {graded}.")
        print("  RULES.md cấm việc này trừ khi coach cho phép; lab.py export sẽ nộp đúng các điểm này.")
        if input("  Coach đã cho phép bằng văn bản? Gõ 'co' để tiếp tục: ").strip().lower() != "co":
            die("Đã hủy. Chạy không có --into-graded để dùng task riêng.")

    step("1. Kết nối CVAT và kiểm model")
    cv = lab.Cvat(args.url)
    cv.login(args.user)
    try:
        funcs = cv.get("/api/lambda/functions")
    except RuntimeError as e:
        die(f"CVAT không gọi được Nuclio ({e}).",
            "Bật CVAT kèm components/serverless/docker-compose.serverless.yml và xóa docker-compose.override.yml "
            "trỏ nuclio về 127.0.0.1 (xem hướng dẫn cài model).")
    funcs = funcs if isinstance(funcs, list) else funcs.get("results", [])
    if not any(f.get("id") == FUNCTION for f in funcs):
        die(f"Chưa thấy function {FUNCTION}. Đang có: {', '.join(f.get('id', '?') for f in funcs) or '(không có)'}",
            "Deploy: nuctl deploy --project-name cvat --path serverless/pytorch/mmpose/hrnet32/nuclio ...")
    ok(f"Model {FUNCTION} sẵn sàng")

    proj = cv.project(rec.get("project", lab.PROJECT))
    if not proj:
        die("Không thấy project d04-keypoint.", "Chạy 'python lab.py setup ...' trước.")

    step(f"2. Task đích: {target}")
    t = cv.task(proj["id"], target)
    if not t:
        files = lab.download([{"file": f} for f in rec["persons"]], lab.DATA / "persons")
        print(f"  Đang tạo {target} và tải {len(files)} ảnh lên...")
        t = cv.post("/api/tasks", {"name": target, "project_id": proj["id"]})
        cv.upload(t["id"], files, args.quality)
        ok(f"Đã tạo {target}")
    else:
        existing = cv.get(f"/api/tasks/{t['id']}/annotations")
        if existing.get("shapes") and not args.rerun:
            die(f"{target} đã có {len(existing['shapes'])} nhãn.",
                "Thêm --rerun để xóa và chạy lại model (nhãn tay trong task này sẽ mất).")
        ok(f"{target} đã có, dùng lại")

    step("3. Chạy model")
    rq = cv.post("/api/lambda/requests", {
        "function": FUNCTION, "task": t["id"], "cleanup": bool(args.rerun),
        "threshold": args.threshold, "mapping": MAPPING,
    })
    wait_lambda(cv, rq["id"])
    ok("Model chạy xong")

    step("4. Hậu xử lý theo luật lab")
    meta = cv.get(f"/api/tasks/{t['id']}/data/meta")
    frames = meta["frames"]
    names = {}
    for l in cv.all(f"/api/labels?project_id={proj['id']}"):
        if l.get("parent_id"):
            names[l["id"]] = l["name"]
        for s in l.get("sublabels") or []:
            names[s["id"]] = s["name"]
    ann = cv.get(f"/api/tasks/{t['id']}/annotations")
    new, log = postprocess(ann, frames, names, min_height=args.min_height)
    cv.req("PUT", f"/api/tasks/{t['id']}/annotations", new, timeout=300)
    for f, hgt in log["removed_small"]:
        print(f"  xóa người cao ~{hgt}px < {args.min_height}px ở {f} (đếm vào báo cáo mục 2)")
    for f, p, x, y in log["clipped"]:
        print(f"  {f}: {p} ở ({x},{y}) ngoài ảnh → outside")
    if log["removed_empty"]:
        print(f"  xóa {len(log['removed_empty'])} skeleton không còn điểm thấy được")

    step("5. Kết quả")
    for fr in frames:
        print(f"  {fr['name']:22s} {log['kept'].get(fr['name'], 0)} người")
    jobs = cv.all(f"/api/jobs?task_id={t['id']}")
    print("\nPHẢI XEM TAY trước khi dùng (model không làm được):")
    print("  - Cờ occluded (v = 1): model không bao giờ đặt; điểm bị che vẫn ra v = 2 → bấm Q.")
    print("  - Người quay lưng / nghiêng: kiểm đổi trái/phải (043719, 518966).")
    print("  - Người bị sót (vd. người ngồi sau xe máy 183176), người trên poster / màn hình bị gán thừa.")
    print("  - Người sát ngưỡng 60 px: script ước lượng, đo lại bằng khung bao.")
    if jobs:
        print(f"\nMở job: {args.url}/tasks/{t['id']}/jobs/{jobs[0]['id']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
