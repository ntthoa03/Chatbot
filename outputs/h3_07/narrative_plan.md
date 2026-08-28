# Narrative plan — H3-07

## Audience and objective

- Audience: lãnh đạo MIMA và nhóm kỹ thuật bàn giao.
- Objective: trong 15 phút chứng minh một AI core có thể phục vụ khách hàng khác ngành bằng tenant config và dữ liệu riêng, đồng thời giữ tool/guardrail/đo lường chung.
- Không trình diễn danh sách tính năng; mỗi phần demo phải quay lại thông điệp đa tenant.

## Narrative arc and slide list

1. **Một hệ thống, nhiều ngành** — nêu thông điệp và 5 tenant đã chạy.
2. **Cùng một luồng, khác tri thức và cách ứng xử** — chuyển MIMA sang phòng khám bằng public key/config.
3. **Năng lực dùng chung có kiểm soát** — tool tên miền và guardrail là hai bằng chứng, không phải hai sản phẩm riêng.
4. **Đo bằng dữ liệu, không chỉnh theo cảm giác** — 4 số đo và phạm vi dataset.
5. **Từ demo đến production** — phần đã chứng minh, phần Hiếu thay bằng hạ tầng thật, quyết định tiếp theo.

## Source plan

- 5 tenant và thời gian onboard: `outputs/h3_01/onboarding_times.csv`.
- Eval/cost/latency: `outputs/h2_09/h2_09_comparison.json`, routed run `20260821T062712.308909Z`, dataset 60 câu.
- Guardrail: `outputs/h2_03/H2-03-summary.json`, 30/30 trap case bị chặn và chuyển người.
- Contract/API: `contract.md`, `api/main.py`.

## Visual system

- 16:9; nền navy đậm, accent tím MIMA và xanh ngọc cho trạng thái an toàn.
- Motif: một lõi trung tâm nối tới các tenant; card lớn, ít chữ, nhấn số liệu.
- Title Poppins/Aptos Display; body Lato/Aptos; toàn bộ chữ và sơ đồ quan trọng là đối tượng PowerPoint chỉnh sửa được.
- Không dùng ảnh thương hiệu/website để tránh phụ thuộc mạng và bản quyền; visual dùng hình khối, connector và card native.

## Editability and fallback plan

- Tiêu đề, số liệu, nhãn tenant, flow và speaker notes đều editable.
- Mỗi slide có speaker notes theo đúng mốc kịch bản.
- Preview 5 slide được ghép thành video MP4 dự phòng không cần mạng/API.
