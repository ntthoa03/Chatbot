# HOA-17 — báo cáo hoàn chỉnh

Ngày chạy: 14/08/2026. Bộ chấm: `eval/cases.yaml`, 30 case độc lập, temperature 0, cùng tenant/config version và cùng phép chấm. Mỗi profile chỉ đổi đúng một biến so với baseline.

## Kết luận báo cáo

Giữ cấu hình production hiện tại (`baseline`). Đây là cấu hình có tỷ lệ đúng cao nhất; không tinh chỉnh nào trong năm thử nghiệm cải thiện chất lượng. Không áp dụng một cấu hình rẻ hơn nếu nó làm giảm tỷ lệ đúng.

Ba con số của cấu hình được chọn:

- Tỷ lệ đúng: **70,00%** — 21/30 case.
- Chi phí trung bình mỗi hội thoại: **11,86 VND** — tổng 355,84 VND/30 lượt; 16 lượt gọi model và 14 lượt xử lý cục bộ 0 VND.
- Độ trễ trung bình: **1.668,97 ms/hội thoại**.

Baseline hoàn thành trong 116,907 giây. Cả sáu profile đều hoàn thành dưới 5 phút/profile và không có lỗi hạ tầng.

## Bảng các cấu hình đã thử

| Profile | Biến duy nhất thay đổi | Giá trị | Đúng | Δ đúng | Chi phí TB (VND) | Δ chi phí | Độ trễ TB (ms) | Δ độ trễ | Thời gian (s) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| **baseline — chọn** | Không | Production: 40 chunk, max 679 ký tự; top-k 5; min-score 0,65; prompt hiện tại; Gemini chính | **70,00%** | — | **11,86** | — | **1.668,97** | — | 116,907 |
| chunk_350 | Chunk size | Tối đa 350 ký tự, overlap 60 | 66,67% | -3,33 điểm % | 12,53 | +0,67 | 1.692,13 | +23,16 | 116,719 |
| top_k_3 | Top-k | 3 thay cho 5 | 60,00% | -10,00 điểm % | 10,68 | -1,18 | 1.225,00 | -443,97 | 155,867 |
| threshold_070 | Ngưỡng điểm | 0,70 thay cho 0,65 | 66,67% | -3,33 điểm % | 9,53 | -2,33 | 1.162,60 | -506,37 | 116,885 |
| prompt_clear_refusal | Prompt | Thêm chỉ dẫn từ chối/thiếu dữ liệu rõ ràng | 60,00% | -10,00 điểm % | 12,30 | +0,44 | 1.319,90 | -349,07 | 116,836 |
| model_gpt_primary | Model chính | GPT-5.6 Luna; Gemini fallback | 65,52% | -4,48 điểm % | 7,51 | -4,35 | 2.180,50 | +511,53 | 116,780 |

Tiêu chí chọn cố định: loại profile có lỗi hạ tầng; ưu tiên tỷ lệ đúng; nếu bằng nhau, ưu tiên chi phí rồi độ trễ thấp hơn. Theo tiêu chí này, baseline thắng khách quan.

## Các case baseline chưa đạt

`Q006`, `T001`, `T006`, `T009_ASCII`, `Q015_ASCII`, `T010`, `T011_ASCII`, `T012`, `T014`.

Nhóm tồn đọng chính là cờ `need_human` chưa bật dù nội dung có đề nghị tư vấn, từ chối dữ liệu riêng tư chưa rõ, thiếu đồng cảm khiếu nại, và fallback “chưa đủ dữ liệu” chưa phù hợp cho OTP/y tế/bài tập. Đây là backlog hành vi/guardrail riêng; thay chunk, top-k, threshold, prompt hoặc model đơn lẻ trong ma trận này không giải quyết tốt hơn baseline.

## Kịch bản demo

10 câu demo đã được chọn từ các luồng đạt baseline, bao phủ giá/RAG, hotline, tool tên miền, prompt injection, cam kết SEO, giảm giá, hoàn tiền và chê đối thủ. File chạy: `eval/demo_cases.yaml`; hướng dẫn thuyết trình: `docs/HOA-17-kich-ban-demo.md`.

```powershell
python -m eval.run --cases eval/demo_cases.yaml
```

## Dữ liệu audit

- `hoa17_tuning_results.csv`: bảng tổng hợp mở trực tiếp bằng Excel.
- `hoa17_tuning_results.json`: bảng tổng hợp có kiểu dữ liệu đầy đủ.
- `reports/<profile>/`: JSON audit, CSV chi tiết, bảng summary và scorecard của từng profile.
- `reports/baseline/*.scorecard.csv`: ba KPI cùng câu hỏi/reply sai của cấu hình được chọn.
- `indexes/chunks_350/`: index thử nghiệm độc lập, không ghi đè index production.
