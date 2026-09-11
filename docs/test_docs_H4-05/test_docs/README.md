# Bộ tài liệu test — H4-04/H4-05 (ingest đa định dạng)

6 file, đại diện đủ các định dạng task yêu cầu: PDF (text-layer), PDF (scan,
cần OCR), Word, Excel, CSV, ảnh chụp. Nội dung là **bảng giá/chính sách mới**,
bổ sung cho các mảng mà `seed_chunks.json` hiện đang nói chung chung không có
số cụ thể (SEO theo mức cạnh tranh, giá Ads, giá tên miền/hosting, giá
backlink, chính sách bảo hành) — đúng mục đích thí nghiệm H4-05: đo xem thêm
tài liệu thật có nâng điểm eval hay không.

## Danh sách file và bẫy kỹ thuật cố ý đưa vào

| File | Định dạng | Bẫy cố ý |
|---|---|---|
| `bang_gia_thiet_ke_web.csv` | CSV | Dùng `;` làm delimiter (không phải `,`) — đúng kiểu Excel Việt Nam hay xuất ra vì `,` đã dùng làm dấu thập phân. Có BOM ở đầu file. Có 1 cột chứa nhiều dòng trong 1 ô (cần parser CSV xử lý quote/escape đúng, không phải split theo `\n` ngây thơ). |
| `bang_gia_seo.xlsx` | Excel | Header **không nằm ở dòng 1** (3 dòng tiêu đề công ty phía trên). **2 bảng khác nhau trong cùng 1 sheet**, không có ranh giới rõ ràng giữa chúng. Có ghi chú tự do rải rác ở cột F, lẫn vào vùng có bảng. Có **sheet phụ thứ 2** (`Dieu khoan`) chứa điều khoản quan trọng, dễ bị bỏ sót nếu pipeline chỉ đọc sheet đầu tiên. |
| `chinh_sach_bao_hanh.docx` | Word | Có heading nhiều cấp, danh sách đánh số + bullet, và 1 bảng — test khả năng giữ đúng cấu trúc khi convert sang chunk (đừng làm phẳng bảng thành text lộn xộn). |
| `bang_gia_quang_cao_ads.pdf` | PDF **có text layer** | Trường hợp dễ nhất trong 6 file — đọc thẳng bằng `pdftotext`/`pdfplumber`, không cần OCR. Dùng để đối chứng: nếu pipeline xử lý sai cả file này thì lỗi nằm ở bước chuẩn hoá chunk, không phải ở OCR. |
| `bang_gia_ten_mien_hosting_SCAN.pdf` | PDF **dạng scan** | Đã xác nhận **0 ký tự** trích được bằng `pdftotext` — bắt buộc phải OCR. Chất lượng ảnh còn tốt (như scan bằng máy scanner phẳng), không nghiêng nhiều, không nhiễu nặng — đây là ca "dễ" trong nhóm cần OCR. |
| `anh_chup_bang_gia_backlink.jpg` | Ảnh chụp | Khó nhất: nghiêng 2.3°, ánh sáng chiếu lệch (gradient sáng/tối theo góc), nhiễu hạt giống camera điện thoại thiếu sáng, giảm nét nhẹ, nén JPEG chất lượng thấp (quality=55, giống ảnh gửi qua Zalo/Messenger bị nén lại nhiều lần). Đây là ca thật sự khó — đúng như trải nghiệm khách hàng gửi ảnh chụp bảng giá dán ở văn phòng. |

## ⚠️ Phát hiện quan trọng khi test — cần bạn xử lý trước khi chạy OCR thật

Máy chủ hiện tại **chỉ có gói ngôn ngữ `tesseract-ocr-eng`**, **chưa có
`tesseract-ocr-vie`**. Test thử OCR bằng tiếng Anh trên file scan tên miền
cho kết quả đọc được nội dung nhưng **mất hết dấu tiếng Việt** (`"Cập nhật"`
→ `"Cap nhat"`, `"Uptime 99.9%, hoàn tiền"` → `"Uptime 99.9%, hoan tien"`).

**Nếu pipeline OCR thật của bạn cũng thiếu gói ngôn ngữ này, mọi tài liệu
scan/ảnh chụp sẽ bị mất dấu** — ảnh hưởng nghiêm trọng đến chất lượng
retrieval (embedding câu có dấu vs không dấu đã là 2 vector khác nhau, mất
dấu do OCN lỗi còn tệ hơn vì mất luôn thông tin, không phải người dùng chủ
động gõ không dấu). Cần cài thêm trước khi chạy thật:

```bash
sudo apt-get install tesseract-ocr-vie
# rồi gọi: pytesseract.image_to_string(image, lang="vie")
```

## Cách test nhanh (không cần chờ pipeline H4-04 xong)

```bash
# CSV — thử python csv module có tự nhận đúng delimiter ; không
python3 -c "import csv; print(list(csv.reader(open('bang_gia_thiet_ke_web.csv', encoding='utf-8-sig'), delimiter=';')))"

# Excel — xem markitdown "hiểu sai" cấu trúc 2 bảng như thế nào
markitdown bang_gia_seo.xlsx

# Word — convert xem giữ đúng bảng/heading không
pandoc -t markdown chinh_sach_bao_hanh.docx

# PDF text-layer — đọc thẳng
pdftotext -layout bang_gia_quang_cao_ads.pdf -

# PDF scan — xác nhận phải OCR (kết quả phải là chuỗi rỗng)
pdftotext -layout bang_gia_ten_mien_hosting_SCAN.pdf -

# Ảnh chụp — xem OCR tiếng Anh đọc sai/thiếu thế nào (khi có tesseract-ocr-vie thì đổi lang="vie")
python3 -c "
import pytesseract
from PIL import Image
print(pytesseract.image_to_string(Image.open('anh_chup_bang_gia_backlink.jpg'), lang='eng'))
"
```

## Gợi ý dùng cho thí nghiệm H4-05

1. Chạy eval baseline (chưa nạp 6 file này) — ghi lại điểm.
2. Nạp cả 6 file qua pipeline H4-04, chuẩn hoá về format chunk đang dùng.
3. Chạy lại eval — so sánh điểm đúng và tỷ lệ "có đủ dữ liệu".
4. Nếu điểm tăng mạnh riêng ở các câu hỏi liên quan tới SEO/Ads/tên
   miền/backlink (những mảng mà tài liệu cũ không có số cụ thể) → bằng chứng
   rõ ràng nút thắt nằm ở **thiếu dữ liệu**, không phải kỹ thuật RAG.
5. Nhớ đúng ràng buộc của task: **chỉ thêm tài liệu, không sửa prompt hay
   chunk size trong lần chạy so sánh này** — đổi nhiều biến cùng lúc thì
   không biết điểm tăng là nhờ đâu.
