# L3B Architecture Record

## System overview

`day09 run` đọc từng case và ghi `case_received`. `solve_case` tạo một `Investigation` riêng cho case đó, giao việc theo thứ tự: entity → customer → order/product → payment/refund → shipment → policy → verifier. Mỗi vai trò bàn giao kết quả cho coordinator qua trạng thái trong bộ nhớ và sự kiện `handoff`. CLI kiểm tra JSON output theo schema rồi ghi `case_finalized`.

Các vai trò là các bước Python quyết định theo bằng chứng. Hệ thống hiện không dùng LLM; kết quả và trace không phụ thuộc prompt hoặc model ngoài.

## Agent ownership

| Actor | Input | Trách nhiệm | MCP tools | Handoff |
| --- | --- | --- | --- | --- |
| coordinator | case | Giao việc theo thứ tự, giữ `case_id` | không gọi tool nghiệp vụ | từng specialist |
| entity-agent | claimed ID, candidate IDs | Xác nhận ID đơn và đối chiếu snapshot theo ngày mở khiếu nại | `get_order` | order đã resolve hoặc trạng thái ambiguous/not_found |
| customer-agent | customer hint | Kiểm tra lịch sử và chọn bản ghi mua gần nhất trước `opened_at` | `get_customer_history` | customer context và bản ghi được chọn |
| order-agent | resolved order | Lấy item cùng kỳ mua, tính tiền hàng và seller | `get_order_items` | item/seller và tổng tiền hàng |
| payment-agent | resolved order, purchase time | Lọc capture/refund cùng kỳ mua, đối chiếu item total | `get_payment_timeline`, `get_refund_timeline` | payment verdict, totals |
| shipment-agent | resolved order, purchase time | So carrier/delivery/estimate và shipping limit hiện tại | `get_shipment_summary` | shipment verdict, seller giao trễ |
| policy-agent | evidence summary, policy version | Chọn issue theo dữ liệu hiện tại, lấy action/refund từ policy | `get_policy` | proposed output |
| verifier-agent | proposed output, MCP refs | Kiểm tra có bằng chứng và tiền hoàn không vượt phần còn lại | không gọi MCP | verified output |

## Entity resolution và A2A

`customer_request.claimed_order_id` chỉ là gợi ý. Entity agent xác nhận mã đơn qua `get_order`; customer agent xem các bản ghi lịch sử của candidate. Khi có nhiều lần mua cùng `order_id`, ưu tiên lần có hạn giao gần nhất đã qua trước `opened_at`; nếu chưa có hạn giao nào đã qua thì lấy lần mua gần nhất trước `opened_at`. Candidate không khớp bản ghi đã chọn được ghi vào `rejected_candidates`. Nếu không có lịch sử, fallback thử tối đa 5 candidate qua `get_order`. Không suy đoán ID mới.

Mỗi sự kiện trace gắn `case_id` và actor cụ thể. `task_assigned` xuất hiện khi coordinator giao việc; `handoff` khi vai trò hoàn tất; `tool_result_consumed` chỉ xuất hiện sau khi một MCP response hợp lệ thực sự được nhận và sử dụng. Không ghi nội dung suy luận riêng.

## Evidence và conflict lifecycle

`EvidenceGateway.call` xác thực envelope theo `mcp-evidence-response-v1`; `Investigation.get` cache trong từng case theo tên tool và tham số, giữ nguyên `evidence_ref`, rồi emit trace. Evidence được chọn cho output theo nhóm liên quan đến issue. Không tái sử dụng evidence giữa case.

Các hàng lịch sử trong customer, item, payment và shipment có thể trùng order ID. `opened_at` và lịch sử khách hàng xác định lần mua đang bị khiếu nại; item và shipping limit được lọc trong 5 ngày từ lần mua, capture trong 2 ngày. Item trùng mã được tính một lần. Với claim thanh toán chia nhiều phương thức, payment agent đối chiếu tổ hợp giao dịch có thứ tự và phương thức khác nhau với tổng tiền item; giao dịch thừa trong MCP timeline không tự động được cộng vào đơn đang xét. Refund được lọc trong khoảng từ lần mua đến tối đa 14 ngày sau khi mở khiếu nại. Khi `get_order` và lịch sử chứa snapshot khác nhau, output ghi `data_conflicts` và chọn bản ghi phù hợp thời gian. Claim không được tự động coi là sự thật.

## Failure và efficiency

| Failure | Retry budget | Fallback | Trace |
| --- | ---: | --- | --- |
| MCP tool trả lỗi | 0 | Thiếu domain đó; không tự tạo evidence | Không emit `tool_result_consumed` |
| Candidate không tìm thấy | 0 | Loại candidate, giữ ambiguous/not_found nếu cần | `handoff` entity |
| Lịch sử mâu thuẫn | 0 | Dùng bản ghi order hiện tại, khai báo conflict | `handoff` policy |
| Thiếu policy hoặc order | 0 | `insufficient_evidence` | `policy_decided` |

Tool discovery được cache trên gateway. Mỗi lời gọi tool với cùng tham số được cache trong một `Investigation`; không có quét tìm trên toàn gateway. Truy vấn sản phẩm, seller và bản sao payment list đã được loại khi không đóng góp vào kết luận. Refund timeline chỉ được gọi cho claim hoàn tiền. Chạy từng case tuần tự để không lẫn evidence scope. Mã không tự retry một lỗi nghiệp vụ để tránh tăng số call audit.

## Verification invariants

- Output phải dùng đúng `case_id` và schema L3B; CLI kiểm tra trước khi ghi file.
- Kết luận khác `insufficient_evidence` cần có MCP evidence refs.
- Mọi ref trong output lấy từ envelope đã nhận của chính case; không sinh hoặc sửa ref.
- Số hoàn tiền đề xuất không vượt số captured còn lại nếu có thể tính số này.
- Confidence nằm trong [0, 1]; payment và shipment chưa đủ dữ liệu giữ verdict `insufficient_evidence`.
- CLI `validate` kiểm tra 100 output, trace schema và inventory trước khi package.

## Reproducibility

Python 3.11+; dependency theo `pyproject.toml`. Không dùng random hoặc LLM. Chạy `day09 validate-inputs`, `day09 run`, `day09 validate`, `day09 package --output dist/submission.zip`. `.env` giữ endpoint và key ở máy cá nhân; không đưa vào source, trace, output hoặc ZIP.
