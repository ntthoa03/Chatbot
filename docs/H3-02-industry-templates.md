# H3-02 — Template config theo ngành

## 1. Chọn ngành theo dữ liệu MIMA

Ngày 26/08/2026, danh mục dự án công khai tại `https://mimadigi.com/du-an` có số lượng cao nhất:

| Hạng | Category MIMA | Số dự án | Template trọng tâm |
|---:|---|---:|---|
| 1 | Xây dựng | 13 | `construction` |
| 2 | Thương mại | 11 | `commerce` |
| 3 | Dịch vụ | 10 | `services` |
| 4 | Cơ khí | 7 | Chưa tách template |
| 5 | Ăn uống | 4 | Chưa tách template |

Số liệu nằm tại `outputs/h3_02/mima_portfolio_industry_counts.json`. Đây là số dự án portfolio công khai, không phải tổng khách trong CRM. Khi có thống kê CRM, cần dùng CRM để xác nhận lại thứ tự.

H3-02 chỉ coi `construction`, `commerce`, `services` là ba template trọng tâm. `medical` và `retail` được giữ làm template bổ trợ vì yêu cầu nghiệp vụ ban đầu đã có guardrail y tế và tool tra đơn; không tiếp tục tạo template riêng cho mọi category còn lại.

## 2. Template đã chuẩn bị

| Template | Phần dùng chung được đóng gói |
|---|---|
| `construction` | Persona thu thập loại công trình/quy mô/địa điểm; chặn cam kết tiến độ, giấy phép, kết cấu; định tuyến báo giá dự án và hợp đồng sang chuyên viên |
| `commerce` | Persona hàng hóa; phân biệt giá công khai với giá sỉ/phân phối; bật `check_order`; không bịa tồn kho hoặc trạng thái đơn |
| `services` | Tư vấn theo nhu cầu; thu lead sau ba lượt; handoff khi hỏi hợp đồng/khiếu nại, khách bức xúc hoặc bot bí hai lần |
| `medical` (bổ trợ) | Guardrail y tế và tool đặt lịch |
| `retail` (bổ trợ) | Luồng bán lẻ và tool tra đơn |

## 3. Cơ chế kế thừa

`ai_core/config.py` nạp cấu hình theo thứ tự:

```text
templates/<industry_template>.yaml defaults
                  ↓ tenant ghi đè sâu
tenants/<tenant_id>.yaml
                  ↓ resolve guardrail_profile
AgentConfig đã validate
```

- Dict được merge sâu.
- List và scalar của tenant thay toàn bộ giá trị template.
- Template không được chứa `tenant_id` hoặc `industry_template` trong `defaults`.
- ID template được kiểm tra bằng allow-list ký tự, không cho path traversal.
- Tenant cũ không khai báo `industry_template` vẫn chạy như trước.

## 4. Tạo tenant mới

Tenant chỉ điền phần riêng:

```yaml
tenant_id: ten_cong_ty
industry_template: construction  # construction | commerce | services
persona:
  bot_name: Trợ lý Tên Công Ty
pricing:
  can_quote: [nhom_co_gia_cong_khai]
  must_contact: [nhom_can_bao_gia_rieng]
contact:
  hotline: null
  zalo: null
knowledge:
  local_index_dir: outputs/<task>/index_ten_cong_ty
```

Kiểm tra config:

```powershell
python -c "from ai_core.config import load_config; print(load_config('ten_cong_ty').model_dump_json(indent=2))"
```

Nếu tenant ghi đè một list như `enabled_tools`, `pricing.can_quote` hoặc `guardrails.forbidden`, tenant phải khai báo lại toàn bộ list đó.

## 5. Acceptance

Tenant 6 dùng `services` đã được tạo và validate trong 28,213 giây, dưới giới hạn 30 phút. Phép đo chỉ gồm tạo YAML phần riêng và load/validate; crawl/index thuộc task sau.

Chạy test:

```powershell
python -m unittest tests.test_h3_02 tests.test_h3_01 tests.test_tenant_isolation -v
```

Điều kiện đạt:

- Có đủ ba template trọng tâm theo dữ liệu portfolio: Xây dựng, Thương mại, Dịch vụ.
- Template chứa hành vi nghiệp vụ riêng, không chỉ đổi tên persona.
- Tenant 6 kế thừa được và onboard dưới 30 phút.
- Năm tenant H3-01 vẫn load được.
- Tenant sai template hoặc path traversal phải lỗi fail-closed.
