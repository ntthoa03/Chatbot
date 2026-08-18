# H2-05 — Kết quả thí nghiệm trước khi sửa code

Thời điểm ghi nhận: 2026-08-18  
Tenant thử nghiệm: `phongkham_hyhy`

## Câu hỏi thí nghiệm

Có thể chạy bot tenant thứ hai chỉ bằng cách thêm YAML, không sửa một dòng code nào không?

## Kết luận trước khi sửa

**Không thể đạt đầy đủ H2-05 chỉ bằng YAML trên trạng thái code hiện tại.** Loader có thể đọc persona/model/guardrail/pricing/tool name từ YAML, nhưng chat không thể chọn index H2-04 theo tenant và không có tool nghiệp vụ y tế để YAML bật.

## Bằng chứng và vị trí chính xác

1. **Index local bị cố định toàn ứng dụng**
   - `ai_core/retriever.py:26`: `DEFAULT_INDEX_DIR = ... / "index"`.
   - `ai_core/retriever.py:103`: tham số `index_dir` mặc định dùng thư mục trên.
   - `ai_core/chat.py:1098`: gọi `retrieve(retrieval_query, request.tenant_id)` mà không truyền index directory.
   - Index mặc định hiện có 40 records và manifest chỉ chứa tenant `mima_internal`.
   - Hệ quả: YAML Hỷ Hỷ có hợp lệ thì retriever vẫn mở index MIMA, sau đó tenant filter trả 0 nguồn. Không rò dữ liệu nhưng bot Hỷ Hỷ không trả lời được nghiệp vụ.

2. **Tool registry chỉ có tool tên miền của MIMA**
   - `ai_core/tools/__init__.py:17`: chỉ đăng ký `CHECK_DOMAIN_TOOL`.
   - YAML chỉ có thể bật/tắt tên tool đã đăng ký; không thể khai báo hành vi tool mới.
   - Có thể đặt `enabled_tools: []` để không dùng nhầm tool tên miền, nhưng chưa thể có tool đặt lịch khám chỉ bằng config.

3. **Prompt/schema còn giả định mọi tenant làm SEO**
   - `ai_core/config.py:144`: `seo_phrasing_example` là trường bắt buộc.
   - `ai_core/prompt.py:63-70`: luôn sinh chỉ dẫn SEO cho mọi tenant.
   - Hệ quả: tenant phòng khám vẫn phải mang một cấu hình SEO vô nghĩa hoặc loader thất bại.

4. **UI có default MIMA nhưng có thể ghi đè, không phải blocker cứng**
   - `app.py:28`: mặc định `AI_CORE_UI_TENANT_ID` là `mima_internal`.
   - Có thể chạy tenant Hỷ Hỷ bằng biến môi trường nên không bắt buộc sửa dòng này.

5. **Audit UI sau khi backend chạy được phát hiện thêm nội dung hiển thị gắn cứng MIMA**
   - `app.py:38` trước khi sửa: tiêu đề khóa là `MIMA — Sale Test`.
   - `app.py:308` trước khi sửa: placeholder chỉ nhắc `website, SEO, tên miền`.
   - Nếu chỉ đổi `AI_CORE_UI_TENANT_ID`, hai dòng này vẫn làm giao diện phòng khám mang nhận diện/nghiệp vụ MIMA. Phát hiện được ghi tại đây trước khi sửa hai dòng UI.

6. **Bảng giá không nằm trong YAML theo thiết kế hiện tại**
   - `ai_core/config.py:160-166`: `pricing` chỉ chứa nhóm được báo/nhóm phải chuyển chuyên viên.
   - Giá thật phải đến từ RAG. Đây là thiết kế an toàn, không phải lỗi, nhưng index đúng tenant là điều kiện bắt buộc.

## Phân loại thay đổi cần thiết

| Vấn đề | Có thể giải quyết bằng YAML? | Cần sửa code? |
|---|---|---|
| Persona/giọng điệu | Có | Không |
| Guardrail không chẩn đoán/kê thuốc | Có, qua output rules | Không |
| Routing bảng giá khác | Có | Không |
| Không bật tool tên miền | Có | Không |
| Chọn index H2-04 theo tenant | Không | **Có** |
| Tool đặt lịch khám mới | Không | **Có** nếu H2-05 bắt buộc phải có tool mới |
| Bỏ đoạn SEO khỏi prompt y tế | Không | **Có** |

File này được tạo **trước mọi thay đổi code nguồn H2-05**, đúng yêu cầu trap “nếu phải sửa code thì đừng sửa vội — ghi lại trước”.
