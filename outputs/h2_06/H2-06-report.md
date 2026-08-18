# H2-06 — Kiểm thử cách ly dữ liệu giữa hai tenant

## 1. Yêu cầu được đối chiếu từ Task-Tuan-2-HOA.xlsx

- Viết automated test chứng minh truy vấn tenant A không bao giờ trả về chunk của tenant B.
- Thử cố ý truyền `tenant_id` sai, thiếu và rỗng; các trường hợp này phải báo lỗi, tuyệt đối không được trả toàn bộ dữ liệu.
- Deliverable chạy được trong CI: `tests/test_tenant_isolation.py`.
- Bẫy bắt buộc: **default deny** — không có `tenant_id` hợp lệ thì dừng xử lý.
- Đây là rủi ro uy tín nghiêm trọng nhất của hệ thống SaaS multi-tenant: rò dữ liệu giữa khách hàng.

## 2. Hiện trạng trước khi vá

### Lỗ hổng 1 — tenant chưa đăng ký bị nuốt lỗi

`retrieve()` từng bắt `ConfigError` khi không tìm thấy tenant rồi tiếp tục bằng cấu hình mặc định. Kết quả thường là danh sách rỗng, nhưng request sai không bị từ chối rõ ràng. Cách xử lý này che giấu lỗi cấu hình/xác thực và không đáp ứng nguyên tắc default deny.

### Lỗ hổng 2 — tenant_id được ghép vào đường dẫn trước khi kiểm tra cú pháp

Config được đọc từ `tenants/{tenant_id}.yaml`, nhưng chưa có allow-list định dạng tenant. Các giá trị như `../mima_internal`, đường dẫn tuyệt đối, chữ hoa hoặc chuỗi có khoảng trắng chưa bị chặn ngay tại biên.

### Lỗ hổng 3 — có thể gọi thẳng vector store

Retriever có kiểm tra tenant rỗng, nhưng lớp local/remote vector store vẫn là public seam có thể bị gọi trực tiếp. Vì vậy việc chỉ vá ở retriever là chưa đủ.

### Phần đã an toàn và được giữ lại

- Local vector store lọc metadata theo đúng `tenant_id` trước khi tính điểm và xếp hạng.
- Remote vector store gửi cả `namespace` và `filter`, sau đó kiểm tra lại tenant trong response.

## 3. Phần đã sửa

| Tầng | Thay đổi | Kết quả fail-closed |
|---|---|---|
| API contract | `ChatRequest.tenant_id` chỉ nhận `^[a-z0-9][a-z0-9_-]{0,63}$` | Thiếu, rỗng, traversal, chữ hoa và khoảng trắng bị Pydantic từ chối |
| Config loader | Thêm `validate_tenant_id()` trước khi tạo đường dẫn YAML | Không thể dùng tenant_id để đi ra ngoài thư mục tenant |
| Registry/config | Tenant đúng cú pháp nhưng chưa có YAML bị `ConfigError` | Tenant chưa đăng ký không được chạy RAG/model |
| Retriever | Không còn nuốt `ConfigError` và dùng default | Tenant sai/không tồn tại phát sinh `RetrieverError` trước embedding |
| Local vector store | Tự kiểm tra tenant scope ở đầu `query()` | Gọi thẳng store với tenant thiếu/sai vẫn bị chặn trước khi load index |
| Local ranking | Chỉ tạo tập ứng viên từ row có tenant khớp rồi mới tính điểm | Chunk tenant khác hoặc thiếu metadata tenant không thể lọt top-k |
| Remote vector store | Tự kiểm tra tenant; gửi `namespace` + `filter` | Request không tenant không được gửi ra mạng |
| Remote response | Kiểm tra lại tenant từng match | Không tin tuyệt đối vào filter phía server; row sai/thiếu tenant bị loại |

Các vị trí chính:

- `ai_core/config.py`: allow-list tenant ID và kiểm tra trước khi đọc file config.
- `ai_core/models.py`: ràng buộc tenant ID ngay trên `ChatRequest`.
- `ai_core/retriever.py`: tenant phải hợp lệ và đã đăng ký trước embedding/query.
- `ai_core/vector_store.py`: bảo vệ độc lập tại cả local và remote store.

### 3.1. Nhật ký đầy đủ các thay đổi trong `ai_core` của H2-06

> Lưu ý về quy trình: các thay đổi dưới đây đã được triển khai trước khi nhật ký này được ghi đầy đủ. Nhật ký được dựng lại từ `git diff` để không bỏ sót. Từ task tiếp theo, nếu audit cho thấy phải sửa `ai_core`, cần ghi **hiện trạng + vị trí + thay đổi đề xuất + ảnh hưởng** vào báo cáo và chờ duyệt trước khi sửa.

| File/vị trí | Hiện trạng được phát hiện trước khi sửa | Thay đổi H2-06 đã thực hiện | Vì sao cần thay đổi | Ảnh hưởng flow cũ | Test xác nhận |
|---|---|---|---|---|---|
| `ai_core/config.py` — đầu file | Chưa có allow-list dùng chung cho tenant ID | Thêm `TENANT_ID_PATTERN = ^[a-z0-9][a-z0-9_-]{0,63}$` | Chỉ chấp nhận ID có định dạng xác định | Tenant production hiện tại dùng chữ thường và `_`, nên vẫn hợp lệ | malformed/path traversal test |
| `ai_core/config.py` — `validate_tenant_id()` | Tenant ID chưa được kiểm tra trước khi dùng trong đường dẫn | Thêm hàm trả tenant hợp lệ hoặc raise `ConfigError` | Chặn rỗng, chữ hoa, khoảng trắng, ký tự đường dẫn và traversal | Request tenant sai trước đây có thể đi sâu hơn; nay dừng sớm | `test_malformed_and_path_traversal_tenant_ids_are_rejected` |
| `ai_core/config.py` — `_load_yaml()` | Tạo `tenants/{tenant_id}.yaml` trực tiếp | Gọi `validate_tenant_id()` trước khi tạo path | Không để input tùy ý tham gia tạo đường dẫn file | Hai YAML `mima_internal` và `phongkham_hyhy` vẫn load bình thường | `test_two_registered_tenants_load_successfully` |
| `ai_core/models.py` — `ChatRequest.tenant_id` | Trường chỉ là `str`, không ràng buộc pattern | Thêm `Field(pattern=...)` | Chặn tenant sai ngay tại biên request | Payload tenant hợp lệ không đổi; payload sai nhận `ValidationError` rõ ràng | Hai test `test_*chat*missing*` |
| `ai_core/retriever.py` — đầu `retrieve()` | Đã chặn `None`/rỗng, nhưng chưa có comment bàn giao | Giữ logic và thêm comment tiếng Việt về default deny | Làm rõ lý do phải dừng trước embedding/index | Không đổi hành vi | Nhóm test empty/whitespace |
| `ai_core/retriever.py` — xử lý `load_config()` | Bắt `ConfigError`, gán config/policy bằng `None`, rồi dùng threshold/margin mặc định | Thay bằng raise `RetrieverError`; bỏ nhánh default cho tenant không đăng ký | Tenant lạ phải lỗi, không được tiếp tục RAG bằng default | Đây là thay đổi hành vi có chủ đích: tenant giả/chưa đăng ký không còn được dùng | `test_unknown_tenant_is_an_error_not_an_empty_result` |
| `ai_core/retriever.py` — cấu hình remote | Có các nhánh xử lý `tenant_config is None` do cơ chế fallback cũ | Dùng trực tiếp embedding policy của tenant đã xác thực | Sau bước fail-closed, config chắc chắn tồn tại | Tenant hợp lệ không đổi; tenant lạ đã bị dừng sớm | unknown tenant test và regression suite |
| `ai_core/vector_store.py` — import/helper | Store chưa tự xác thực tenant nếu bị gọi trực tiếp | Thêm `re` và `_require_tenant_scope()` | Chống đường gọi tắt bỏ qua API/retriever | Caller hợp lệ không đổi | local/remote direct-call tests |
| `ai_core/vector_store.py` — `LocalNumpyVectorStore.query()` | Phụ thuộc caller truyền tenant đúng | Gọi `_require_tenant_scope()` trước `_load()` | Tenant sai phải lỗi trước khi đọc index | Query hợp lệ giữ nguyên | `test_local_store_rejects_empty_tenant_before_loading_index` |
| `ai_core/vector_store.py` — local ranking | Đã lọc row theo tenant trước ranking | Không đổi logic; đổi comment sang tiếng Việt và viết test chứng minh | Xác nhận cơ chế đang có thực sự cách ly A/B | Không đổi | mixed-index và foreign-row tests |
| `ai_core/vector_store.py` — `RemoteVectorStore.query()` | Đã có namespace, filter và lọc response; chưa tự chặn tenant sai trước network | Gọi `_require_tenant_scope()` trước khi tạo payload; thêm comment tiếng Việt | Request sai không được gửi ra vector database | Query hợp lệ giữ nguyên | `test_remote_store_rejects_empty_tenant_before_network` |
| `ai_core/vector_store.py` — `_remote_result()` | Đã loại row không khớp tenant | Không đổi logic; thêm comment tiếng Việt và test server trả dữ liệu độc hại | Ghi rõ phòng thủ lớp cuối, không tin tuyệt đối remote server | Không đổi | `test_remote_request_has_double_filter_and_drops_foreign_response_rows` |

### 3.2. Thay đổi trong cùng file nhưng không phải bản vá H2-06

`git diff` hiện tại của repository còn chứa thay đổi từ các task trước. Không được quy nhầm các phần sau thành H2-06:

- `ai_core/config.py`: `RequestPolicyVariantConfig`, `customer_upset_message`, `seo_phrasing_example` optional — thuộc guardrail/H2-03.
- `ai_core/config.py`: `KnowledgeConfig.local_index_dir` — thuộc việc tách knowledge theo tenant H2-04/H2-05.
- `ai_core/retriever.py`: `PROJECT_ROOT`, `index_dir=None` và chọn index từ `tenant_config.knowledge.local_index_dir` — thuộc luồng chọn knowledge của H2-04/H2-05.

H2-06 chỉ dựa trên các phần multi-tenant có sẵn này để kiểm thử, đồng thời vá cơ chế default-deny được liệt kê tại mục 3.1.

### 3.3. Quy trình bắt buộc cho các thay đổi `ai_core` tiếp theo

1. Audit read-only và tái hiện lỗi bằng input/output cụ thể.
2. Ghi vào báo cáo: file, hàm/dòng, hành vi hiện tại, rủi ro, thay đổi đề xuất và ảnh hưởng flow cũ.
3. **Chưa sửa code ở bước này.** Gửi báo cáo để duyệt.
4. Chỉ sau khi được duyệt mới sửa `ai_core`.
5. Ghi diff thực tế, test riêng task và kết quả regression vào cùng báo cáo.

## 4. Phân biệt chính xác các trường hợp

| Input | Hành vi đúng sau khi vá | Lý do |
|---|---|---|
| Không truyền `tenant_id` | Error | Thiếu khóa phân vùng dữ liệu |
| `tenant_id=None`, `""`, hoặc chỉ khoảng trắng | Error trước embedding/index/network | Default deny |
| `../mima_internal`, `MIMA`, `tenant id`, đường dẫn tuyệt đối | Error | Sai allow-list và có nguy cơ traversal |
| Tenant đúng cú pháp nhưng chưa đăng ký | Error trước RAG/model | Không được âm thầm dùng cấu hình mặc định |
| Tenant A hợp lệ truy vấn đúng kho | Chỉ trả chunk A | Cách ly bình thường |
| Tenant A hợp lệ nhưng bị trỏ nhầm sang index chỉ chứa B | Trả `[]`, không trả B | A vẫn là tenant hợp lệ; kho chỉ không có dữ liệu thuộc A |
| Index dùng chung có cả A và B | Chỉ xếp hạng và trả row của tenant đang hỏi | Lọc trước ranking |
| Remote DB trả row B dù request là A | Loại row B ở client | Phòng thủ nhiều lớp |
| Row không có metadata `tenant_id` | Loại bỏ | Không xác minh được quyền sở hữu nên mặc định từ chối |

Lưu ý: yêu cầu “wrong tenant_id phải error” được áp dụng cho tenant sai cú pháp hoặc chưa đăng ký. Một tenant đã đăng ký nhưng vô tình trỏ vào kho khác không phải input không hợp lệ; hành vi an toàn là trả rỗng, tuyệt đối không trả dữ liệu tenant kia.

## 5. Bộ automated test

File: `tests/test_tenant_isolation.py`

Tổng cộng 15 test, bao phủ:

1. Hai tenant thật `mima_internal` và `phongkham_hyhy` đều có config hợp lệ.
2. Tenant chưa đăng ký báo lỗi và không gọi embedding.
3. Traversal/malformed tenant bị từ chối.
4. Thiếu positional tenant argument báo lỗi.
5. `None`, rỗng và whitespace bị chặn trước embedding.
6. `ChatRequest` từ chối tenant thiếu/rỗng/malformed.
7. Public `chat()` từ chối tenant thiếu/rỗng.
8. Public `chat()` từ chối tenant chưa đăng ký trước RAG/model.
9. Chạy trên hai index thật: MIMA 40 chunk và Phòng khám Hỷ Hỷ 776 chunk; kết quả chỉ thuộc đúng tenant/domain.
10. Cố ý dùng tenant hợp lệ với index của tenant còn lại; kết quả bằng rỗng ở cả hai chiều.
11. Index trộn A/B được lọc đúng cho cả hai tenant.
12. Gọi thẳng local store với tenant sai vẫn bị chặn trước load index.
13. Gọi thẳng remote store với tenant sai vẫn bị chặn trước network.
14. Local store loại row tenant khác và row thiếu tenant trước ranking.
15. Remote request có hai lớp namespace/filter và client loại response tenant khác/thiếu tenant.

## 6. Kết quả chạy

| Phạm vi | Lệnh | Kết quả |
|---|---|---|
| Riêng H2-06 | `python -m unittest tests.test_tenant_isolation -v` | **15/15 pass** |
| Toàn bộ regression | `python -m unittest discover -s tests -p "test_*.py"` | **196/196 pass** |
| Index MIMA thật | đọc `index/metadata.json` | **40 chunk** |
| Index Hỷ Hỷ thật | đọc `outputs/h2_04/index_phongkham_hyhy/metadata.json` | **776 chunk** |

Các test dùng vector đã có hoặc embed giả lập xác định, không gọi Gemini/OpenAI và không phát sinh chi phí API.

## 7. Các bẫy đã tránh

1. **Không coi `[]` là đủ cho tenant chưa đăng ký:** phải phát sinh lỗi rõ ràng.
2. **Không chỉ kiểm tra ở UI/API:** retriever và vector store đều tự bảo vệ để tránh đường gọi tắt.
3. **Không lọc sau global top-k ở local store:** lọc tenant trước ranking, tránh tenant khác chiếm top-k và tránh sai kết quả.
4. **Không chỉ tin remote namespace:** request có namespace + filter, response còn được kiểm tra lại.
5. **Không chấp nhận row thiếu tenant:** thiếu bằng chứng sở hữu nghĩa là deny.
6. **Không ghép tenant tùy ý vào path:** chỉ cho phép ID thuộc allow-list.
7. **Không làm tenant production phụ thuộc default config:** tenant phải được đăng ký bằng YAML riêng.

## 8. Ảnh hưởng tới flow MIMA cũ

- Flow hợp lệ của `mima_internal` vẫn chạy với config và index hiện tại.
- Thay đổi có chủ đích duy nhất ở hành vi lỗi: tenant thiếu, malformed hoặc chưa đăng ký bây giờ dừng sớm thay vì âm thầm trả rỗng/dùng mặc định.
- Hai test cũ từng dùng tenant giả chưa đăng ký đã được đổi sang hai tenant thật, vì hành vi cho tenant giả tiếp tục chạy trái với tiêu chí H2-06.
- Toàn bộ 196 regression test đều pass, nên chưa thấy regression trong các flow tuần trước.

## 9. Cách chạy trong CI hoặc máy local

```powershell
python -m unittest tests.test_tenant_isolation -v
```

Để kiểm tra toàn bộ hệ thống:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Tiêu chí chặn merge/deploy: bất kỳ test isolation nào fail thì pipeline phải fail; không được bỏ qua hoặc đổi lỗi thành danh sách rỗng cho tenant thiếu/sai/chưa đăng ký.
