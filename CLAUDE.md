# video-dubber — ghi chú cho Claude

## Hàng chờ lỗi kỹ thuật (tab 📋 Đánh giá)

Người dùng ghi lỗi kỹ thuật khi xem video (dịch chữ màn hình, che mờ, phụ đề, giọng, cắt cảnh, UI, pipeline), duyệt
xong thì lỗi vào hàng chờ:

- `data/feedback/queue.json`: danh sách lỗi. Mỗi mục có `id`, `job_id`, `job_dir`, `area`, `t` (giây trong file
  `output`), `text` (lời người dùng), `frame` (ảnh khung hình tại `t`), `warnings` (cảnh báo trong log job),
  `steps` (chi tiết từng bước), `status` (`queued` | `fixed`).
- `data/feedback/QUEUE.md`: cùng nội dung, dạng dễ đọc.

Khi người dùng bảo "sửa các lỗi trong hàng chờ":

1. Đọc từng mục `queued`, xem `frame`, mở job (`job_dir`) để tái hiện.
2. Sửa nguyên nhân gốc trong code, thêm test hồi quy, chạy `python -m pytest -q`.
3. Ghi lại vào `queue.json`: `status` = `fixed`, `fix` = đã sửa gì (tiếng Việt, 1–2 câu), `fixed_at` = epoch.
   App tự đồng bộ trạng thái này vào ghi chú của job. Không sửa được thì để `queued` và ghi lý do vào `fix`.
4. Khởi động lại server bằng `POST /api/admin/restart`, chỉ khi không có job đang chạy.

Mọi ghi chú và đánh giá của người dùng đều nằm trong `data/feedback/log.jsonl` (chỉ ghi thêm, không xoá).
