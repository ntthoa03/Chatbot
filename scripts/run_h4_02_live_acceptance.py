"""Run synthetic natural questions through the real H4-01 API, then real H4-02."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from ai_core.interfaces import build_services  # noqa: E402
from api.main import PublicKeyResolver, create_app  # noqa: E402
from scripts.cluster_knowledge_gaps import run as run_h4_02  # noqa: E402
from scripts.verify_h4_01_acceptance import _run_one, _save_reports  # noqa: E402
from storage import SQLiteStore  # noqa: E402


FREQUENCIES = (4, 3, 3, 2, 2, 2, 1, 1, 1, 1)
TENANT_TOPICS: dict[str, list[tuple[str, list[str]]]] = {
    "mima_internal": [
        ("Hỗ trợ kỹ thuật ban đêm", ["MIMA có hỗ trợ kỹ thuật lúc 2 giờ sáng không?", "Ban đêm website lỗi thì có kỹ thuật viên trực không?"]),
        ("Tiếp khách Chủ nhật", ["Văn phòng MIMA có tiếp khách trực tiếp vào Chủ nhật không?", "Chủ nhật tôi có thể đến văn phòng MIMA được không?"]),
        ("Chỗ đậu ô tô", ["Văn phòng MIMA có chỗ đậu ô tô miễn phí không?", "Khách đến MIMA gửi ô tô ở đâu và có mất phí không?"]),
        ("SLA phản hồi sự cố", ["Thời gian phản hồi sự cố tối đa sau bàn giao là bao lâu?", "MIMA cam kết tiếp nhận lỗi website trong bao nhiêu phút?"]),
        ("Quyền sở hữu mã nguồn", ["Sau bàn giao khách hàng có sở hữu toàn bộ mã nguồn không?", "MIMA có bàn giao quyền sở hữu source code cho khách không?"]),
        ("Thời hạn lưu bản sao", ["Các bản sao lưu website được giữ trong bao nhiêu ngày?", "MIMA lưu backup của khách hàng trong thời hạn bao lâu?"]),
        ("Tiêu chuẩn hỗ trợ người khuyết tật", ["Website có đáp ứng chuẩn WCAG 2.2 AA không?"]),
        ("Phí dịch nội dung đa ngôn ngữ", ["Chi phí dịch nội dung website sang tiếng Nhật là bao nhiêu?"]),
        ("Thời gian khôi phục thảm họa", ["Khi máy chủ hỏng hoàn toàn thì thời gian khôi phục tối đa là bao lâu?"]),
        ("Thiết bị phòng họp khách", ["Màn hình trong phòng họp dành cho khách tại MIMA có kích thước bao nhiêu inch?", "Phòng họp tiếp khách của MIMA sử dụng máy chiếu model nào?"]),
    ],
    "phongkham_hyhy": [
        ("Cấp cứu ban đêm", ["Phòng khám có tiếp nhận cấp cứu lúc 1 giờ sáng không?", "Ban đêm có bác sĩ trực cấp cứu tại phòng khám không?"]),
        ("Bảo hiểm y tế", ["Phòng khám có thanh toán bằng bảo hiểm y tế không?", "Thẻ bảo hiểm y tế được áp dụng cho dịch vụ nào?"]),
        ("Khám trẻ em Chủ nhật", ["Chủ nhật có bác sĩ khám cho trẻ em không?", "Tôi có thể đưa bé đến khám vào Chủ nhật không?"]),
        ("Chỗ đậu ô tô", ["Người bệnh đến khám có chỗ đậu ô tô miễn phí không?", "Bãi xe ô tô của phòng khám nằm ở đâu?"]),
        ("Thời gian trả xét nghiệm", ["Xét nghiệm máu mất tối đa bao lâu có kết quả?", "Bao nhiêu giờ thì tôi nhận được kết quả xét nghiệm?"]),
        ("Hỗ trợ xe lăn", ["Phòng khám có lối đi và xe lăn cho người khuyết tật không?", "Người ngồi xe lăn có vào phòng khám thuận tiện không?"]),
        ("Hóa đơn điện tử", ["Phòng khám có xuất hóa đơn điện tử trong ngày không?"]),
        ("Phí hủy lịch", ["Hủy lịch khám sát giờ có bị tính phí không?"]),
        ("Phiên dịch y tế", ["Phòng khám có phiên dịch tiếng Nhật cho bệnh nhân không?"]),
        ("Thời hạn lưu hồ sơ", ["Hồ sơ bệnh án được phòng khám lưu trong bao nhiêu năm?"]),
    ],
    "bat_dong_san_phuoc_thinh": [
        ("Kiểm tra tranh chấp", ["Công ty có xác minh căn nhà đang tranh chấp không?", "Trước khi mua bên mình kiểm tra lịch sử tranh chấp thế nào?"]),
        ("Xem nhà buổi tối", ["Tôi có thể xem nhà sau 9 giờ tối không?", "Bên mình có dẫn khách xem nhà vào buổi tối không?"]),
        ("Phí môi giới", ["Phí môi giới bên nào trả và tỷ lệ bao nhiêu?", "Hoa hồng môi giới mua bán nhà được tính thế nào?"]),
        ("Hoàn cọc khi ngân hàng từ chối", ["Nếu ngân hàng không duyệt vay thì tiền cọc có được hoàn lại không?", "Vay không được thì khách có mất cọc giữ nhà không?"]),
        ("Thời gian sang tên", ["Công ty cam kết sang tên sổ trong tối đa bao nhiêu ngày?", "Thủ tục chuyển tên chủ sở hữu thường kéo dài bao lâu?"]),
        ("Kiểm tra quy hoạch", ["Bên mình cung cấp văn bản xác nhận quy hoạch mới nhất không?", "Khách có được xem kết quả kiểm tra quy hoạch chính thức không?"]),
        ("Bảo hiểm quyền sở hữu", ["Giao dịch có bảo hiểm rủi ro quyền sở hữu bất động sản không?"]),
        ("Đo đạc lại diện tích", ["Trước giao dịch công ty có hỗ trợ đo đạc lại diện tích thực tế không?"]),
        ("Công chứng ngoài giờ", ["Số quyết định cho phép công chứng hợp đồng lúc 22 giờ Chủ nhật là bao nhiêu?", "Văn phòng công chứng nào xác nhận lịch 22 giờ Chủ nhật cho giao dịch?"]),
        ("Bàn giao tài sản gắn liền", ["Danh mục nội thất để lại có được lập biên bản khi bàn giao không?"]),
    ],
    "giao_duc_haiyan": [
        ("Học bù khi nghỉ", ["Học viên nghỉ ốm có được sắp xếp học bù miễn phí không?", "Nếu vắng một buổi thì trung tâm cho học bù thế nào?"]),
        ("Bảo lưu khóa học", ["Khóa học được bảo lưu tối đa bao nhiêu tháng?", "Tôi có thể tạm dừng và giữ lại thời gian học trong bao lâu?"]),
        ("Lớp cuối tuần", ["Trung tâm có lớp tiếng Trung chỉ học thứ Bảy và Chủ nhật không?", "Có khóa nào dành riêng cho người chỉ rảnh cuối tuần không?"]),
        ("Hoàn học phí", ["Sau buổi thứ ba không phù hợp thì có được hoàn học phí không?", "Chính sách hoàn tiền khi học viên muốn dừng khóa là gì?"]),
        ("Sĩ số tối đa", ["Một lớp tiếng Trung có tối đa bao nhiêu học viên?", "Trung tâm giới hạn sĩ số mỗi lớp là bao nhiêu?"]),
        ("Giáo viên thay thế", ["Nếu giáo viên chính nghỉ thì trung tâm bố trí người dạy thay thế nào?", "Lớp có bị hủy khi giảng viên đột xuất vắng không?"]),
        ("Thi thử HSK", ["Trung tâm có tổ chức thi thử HSK miễn phí mỗi tháng không?"]),
        ("Phụ đề bài giảng", ["Video bài giảng có phụ đề tiếng Việt cho người khiếm thính không?"]),
        ("Cam kết đầu ra", ["Nếu không đạt HSK như cam kết thì học viên được hỗ trợ gì?"]),
        ("Thời hạn cấp chứng nhận", ["Sau khi hoàn thành khóa học bao lâu thì nhận được chứng nhận?"]),
    ],
    "thuc_pham_thien_minh": [
        ("Giao hàng đông lạnh", ["Đơn hàng đông lạnh được giữ ở nhiệt độ bao nhiêu khi vận chuyển?", "Xe giao hàng có theo dõi nhiệt độ sản phẩm suốt chuyến không?"]),
        ("Đổi hàng mất lạnh", ["Hàng bị mất lạnh khi giao có được đổi miễn phí không?", "Nếu nhiệt độ giao hàng không đạt thì xử lý đổi trả thế nào?"]),
        ("Giá bán sỉ", ["Đơn bao nhiêu ký thì được áp dụng bảng giá bán sỉ?", "Điều kiện số lượng để nhận giá sỉ là gì?"]),
        ("Truy xuất lô hàng", ["Khách có thể tra nguồn gốc bằng mã lô trên bao bì không?", "Mã lô sản phẩm cho biết được những thông tin nào?"]),
        ("Hạn dùng sau mở gói", ["Sản phẩm sau khi mở gói dùng an toàn trong bao nhiêu ngày?", "Mở bao bì rồi thì phải dùng hết trong thời gian nào?"]),
        ("Kiểm nghiệm dị ứng", ["Sản phẩm có báo cáo kiểm nghiệm các chất gây dị ứng không?", "Tôi có thể xem kết quả xét nghiệm thành phần dị ứng ở đâu?"]),
        ("Thu hồi sản phẩm", ["Khi phát hiện lô hàng lỗi công ty thông báo thu hồi bằng cách nào?"]),
        ("Chứng nhận kho lạnh", ["Kho bảo quản của công ty có chứng nhận an toàn chuỗi lạnh không?"]),
        ("Giao hàng Chủ nhật", ["Đơn sỉ có được giao vào ngày Chủ nhật không?"]),
        ("Bồi thường giao trễ", ["Đơn thực phẩm giao trễ làm hỏng hàng được bồi thường thế nào?"]),
    ],
}


def _cases_for(tenant_id: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for topic_index, ((topic, variants), frequency) in enumerate(
        zip(TENANT_TOPICS[tenant_id], FREQUENCIES, strict=True), start=1
    ):
        for occurrence in range(frequency):
            cases.append(
                {
                    "id": f"{tenant_id}-topic-{topic_index:02d}-{occurrence + 1:02d}",
                    "question": variants[occurrence % len(variants)],
                    "topic": topic,
                    "type": "missing_knowledge_live_api_test",
                }
            )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "h4_02_live")
    args = parser.parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    database = run_dir / "h4_01_live_gaps.sqlite3"
    services = build_services(backend="real")
    storage = SQLiteStore(database)
    h401_summary: dict[str, Any] = {}
    try:
        for tenant_id in TENANT_TOPICS:
            cases = _cases_for(tenant_id)
            tenant_dir = run_dir / "h4_01" / tenant_id
            tenant_dir.mkdir(parents=True, exist_ok=True)
            (tenant_dir / "generated_cases.json").write_text(
                json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            api = create_app(
                services=services,
                storage=storage,
                public_key_resolver=PublicKeyResolver({"h4-01-acceptance-key": tenant_id}),
            )
            rows: list[dict[str, Any]] = []
            with TestClient(api) as client:
                for index, case in enumerate(cases, start=1):
                    row = _run_one(
                        case,
                        client=client,
                        storage=storage,
                        tenant_id=tenant_id,
                        config_version=1,
                        run_id=run_id,
                    )
                    rows.append(row)
                    print(
                        f"H4-01 {tenant_id} [{index:02d}/{len(cases)}] "
                        f"gap={row['gap_logged']} reason={row['gap_reason'] or '-'}",
                        flush=True,
                    )
            _, _, report_path = _save_reports(rows, tenant_dir, run_id, min_gap_cases=10)
            h401_summary[tenant_id] = {
                "case_count": len(rows),
                "gap_count": sum(bool(row["gap_logged"]) for row in rows),
                "error_count": sum(bool(row["error"]) for row in rows),
                "report": str(report_path.resolve()),
            }
    finally:
        storage.close()

    h402_report = run_h4_02(
        database=database,
        tenant_ids=list(TENANT_TOPICS),
        output_dir=run_dir / "h4_02",
        threshold=0.85,
        use_llm=True,
        source_data_label="SYNTHETIC_LIVE_API_TEST — không phải log khách thật",
    )
    checks: dict[str, Any] = {}
    passed = True
    for tenant_id, tenant in h402_report["tenants"].items():
        frequencies = [item["frequency"] for item in tenant["top_missing_topics"]]
        tenant_passed = (
            h401_summary[tenant_id]["gap_count"] >= 10
            and len(tenant["top_missing_topics"]) == 10
            and frequencies == sorted(frequencies, reverse=True)
        )
        passed = passed and tenant_passed
        checks[tenant_id] = {
            "passed": tenant_passed,
            "h4_01": h401_summary[tenant_id],
            "cluster_count": tenant["cluster_count"],
            "top_10_count": len(tenant["top_missing_topics"]),
            "top_10_frequencies": frequencies,
        }
    summary = {
        "schema_version": "h4-02.live-api-acceptance.v1",
        "run_id": run_id,
        "source_data_label": "SYNTHETIC_LIVE_API_TEST — không phải log khách thật",
        "passed": passed,
        "tenants": checks,
    }
    (run_dir / "acceptance_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"H4-02 LIVE API ACCEPTANCE: {'PASS' if passed else 'FAIL'}")
    for tenant_id, result in checks.items():
        print(
            f"{tenant_id}: gaps={result['h4_01']['gap_count']}/20, "
            f"clusters={result['cluster_count']}, top10={result['top_10_count']}, "
            f"frequencies={result['top_10_frequencies']}"
        )
    print(f"Kết quả: {(run_dir / 'acceptance_summary.json').resolve()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
