"""
ai_core — module xử lý hội thoại AI, HOÀN TOÀN tách khỏi DB/ORM/HTTP framework.

Ràng buộc cứng (HOA-02):
- Không import psycopg, sqlalchemy, django, redis, flask, fastapi, ... trong package này.
- Cửa vào duy nhất: ai_core.chat.chat(payload: dict) -> dict
- Mọi dữ liệu cần thiết (lịch sử hội thoại, config tenant, tenant_id) phải đi vào
  qua tham số payload — module này không tự đi lấy dữ liệu ở đâu khác.

Việc resolve tenant an toàn (xác thực, chống rò rỉ chéo tenant) xảy ra ở tầng
gọi module này (backend), KHÔNG xảy ra trong ai_core.

Schema request/response khớp với hợp đồng API đã chốt ở HOA-01 (file
contract.md, không nằm trong package này).
"""

__version__ = "0.1.0"
