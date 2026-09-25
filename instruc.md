# Tóm tắt đề bài và kế hoạch thực hiện — L3B

## Mục tiêu

Xây dựng hệ thống multi-agent điều tra tranh chấp thương mại điện tử. Với mỗi case, hệ thống phải truy vấn bằng chứng có thẩm quyền qua MCP Evidence Gateway, phân tích và đưa ra kết luận nghiệp vụ, ghi trace phối hợp giữa các agent, rồi xuất kết quả JSON đúng contract. Bài nộp được chấm tự động; repo starter không chứa bộ 100 case, đáp án hoặc scoring oracle. Input chính thức được phát hành trên GitHub Release.

Đây là repo **L3B** (`K4-L3B-MultiAgent-MCP-A2A`). Ngoài phân tích đơn hàng, vận chuyển, thanh toán và chính sách, L3B yêu cầu giải quyết định danh đơn hàng từ các candidate, xét lịch sử khách hàng, xử lý nguồn dữ liệu mâu thuẫn và tiết kiệm số lần gọi MCP.

## Nguồn chuẩn và hiện trạng repo

- `contracts/schemas/` là nguồn chuẩn cho cấu trúc output L3B, trace, manifest và MCP response. Không thêm field ngoài schema; khi tài liệu mô tả khác schema, ưu tiên schema.
- `contracts/scoring/scoring-policy-v2.json` định nghĩa trọng số, hard gates và các quy tắc chấm công khai.
- `src/student_agent/workflow.py` đã triển khai `solve_case(case, gateway, trace)` theo các vai trò entity, customer, order, payment, shipment, policy và verifier. Quy tắc cần tiếp tục hiệu chỉnh theo feedback chấm điểm.
- CLI `day09` đã có các lệnh `mcp-tools`, `validate-inputs`, `run`, `validate`, `package`. `run` gọi `solve_case` cho từng case; mã đóng gói và kiểm tra schema đã có sẵn.
- `ARCHITECTURE.md` đã mô tả luồng và quyền dùng tool của từng vai trò.
- Bộ input L3B đã được giải nén vào `l3b-inputs-v1/` và sao chép vào `inputs/` cùng `case-set.json` ở gốc repo. Raw input và output được `.gitignore` loại khỏi Git.

## Các việc cần làm theo thứ tự

1. **Đăng ký và chuẩn bị môi trường.** Mỗi thành viên đăng ký trên Competition Workspace theo hướng dẫn lớp, khai báo đúng team và lưu Team API Key chỉ hiển thị một lần. Fork repo L3B và **giữ nguyên tên repo**. Dùng Python 3.11+, tạo `.venv`, chạy `python -m pip install -e ".[dev]"`, sao chép `.env.example` thành `.env`, điền `COMPETITION_API_URL`, `COMPETITION_TEAM_API_KEY`, `MCP_ENDPOINT`. Không commit `.env` hoặc chia sẻ API key công khai. Chạy `pytest -q`, `day09 --help`, `day09 mcp-tools`; dùng kết quả discovery để biết tên và tham số tool thực tế.
2. **Thiết kế workflow L3B.** Trong `src/student_agent/workflow.py`, xây coordinator/router và các vai trò phù hợp: entity/customer, order/product, shipment, payment/refund, policy/conflict, verifier. Quy định input/output khi handoff, quyền dùng tool, cách chọn hoặc loại candidate, xử lý mâu thuẫn, retry có giới hạn và cache trong phạm vi case. Cập nhật `ARCHITECTURE.md` bằng thiết kế đã triển khai.
3. **Thu thập và dùng bằng chứng.** Mọi MCP call phải truyền đúng `case_id`. Chỉ dùng `evidence_ref` nguyên gốc từ gateway, đúng team/run/case; không tự tạo, sửa hoặc tái sử dụng chéo case. Chỉ trích dẫn bằng chứng hỗ trợ kết luận; khi tiêu thụ kết quả tool, emit `tool_result_consumed` với `tool_name` và `evidence_refs`. Hạn chế gọi lại, retry hoặc quét rộng vì mọi call được audit và tính vào efficiency.
4. **Lập kết luận và kiểm chứng.** Điền đầy đủ các trường bắt buộc của `l3b-output-v2.schema.json`: assessment, affected_entities, entity_resolution, customer_context, shipment_analysis, payment_analysis, root_cause_analysis, evidence_refs, data_conflicts, financial_resolution và resolution_actions. Xác định lỗi chính, bên chịu trách nhiệm, số tiền hoàn BRL, hành động xử lý; đối chiếu timeline, thanh toán/hoàn tiền, candidate bị loại và nguồn mâu thuẫn. Hiệu chỉnh `confidence` trong [0, 1] theo chất lượng bằng chứng. Verifier phải kiểm tra tính nhất quán, phạm vi và liên kết bằng chứng trước khi trả output.
5. **Ghi trace quan sát được.** Ghi đúng schema `trace-event-v1.schema.json`. Luồng cần thể hiện `case_received`, `task_assigned`, `handoff`, `tool_result_consumed` khi dùng evidence, `policy_decided`, `verification_completed`, `case_finalized`. CLI đã ghi `case_received` và `case_finalized`; workflow cần ghi các sự kiện còn lại khi thực sự xảy ra. Không tạo trace giả: MCP server có audit độc lập.
6. **Chạy bộ đề chính thức.** Tải ZIP input **L3B** từ GitHub Release và giải nén để có `case-set.json` cùng `inputs/L3B_CASE_*.json`. Chạy `day09 validate-inputs`, `day09 run`, `day09 validate`. Kết quả cần đủ đúng 100 file `outputs/<case_id>.json` và `traces/trace.jsonl` hợp lệ. Kiểm tra thêm chất lượng nghiệp vụ và provenance vì pass schema chưa bảo đảm điểm tốt.
7. **Đóng gói và nộp.** Chạy `day09 package --output dist/submission.zip`. ZIP phải có trực tiếp ở gốc `manifest.json`, `trace.jsonl`, `outputs/` với đúng 100 JSON; không có thư mục bọc ngoài, source, raw input, `.env`, key hay debug log. Upload ZIP tại Competition Workspace mục L3B và chọn submission final theo hướng dẫn trên workspace.

## Tiêu chí chấm điểm L3B

| Thành phần | Trọng số |
| --- | ---: |
| Đúng kết luận nghiệp vụ (`semantic`) | 40% |
| Chất lượng bằng chứng (`evidence`) | 15% |
| Bằng chứng khớp MCP audit (`provenance`) | 15% |
| Nhất quán giữa các trường (`consistency`) | 10% |
| Đúng JSON Schema (`schema`) | 5% |
| Confidence hợp lý (`calibration`) | 5% |
| Phối hợp agent trong trace (`workflow`) | 5% |
| Hiệu quả số MCP calls (`efficiency`) | 5% |

**Điều kiện có thể khiến bài nhận 0 điểm:** thiếu/sai case ID hoặc không đủ 100 case; output không chấm được theo schema; thiếu evidence bắt buộc; `evidence_ref` không tồn tại hoặc sai team/run/case; ZIP sai cấu trúc, thiếu manifest hoặc trace. Ưu tiên kiểm tra các điều kiện này trước khi tối ưu điểm thành phần.

## Mốc hoàn thành

- Kết nối MCP và unit tests starter chạy được.
- `solve_case` xử lý được case L3B thực tế, có evidence thật và trace hợp lệ.
- `ARCHITECTURE.md` phản ánh đúng workflow đã viết.
- `day09 validate-inputs`, `day09 run`, `day09 validate`, `day09 package --output dist/submission.zip` đều thành công với bộ 100 case chính thức.
