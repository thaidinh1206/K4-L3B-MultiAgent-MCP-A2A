# L3B Architecture Record

Tài liệu thiết kế kiến trúc hệ thống Multi-Agent điều tra khiếu nại thương mại điện tử (K4 L3B).

## 1. System overview

Luồng xử lý từ input case, candidate resolution, điều phối đa chuyên gia, đối soát bằng chứng MCP, xử lý xung đột, thẩm định tính toàn vẹn đến output JSON và trace.

```text
Input Case
    │
    ▼
[Coordinator Agent] ────────────────────────── (Trace: case_received)
    │
    ├─► [Entity Resolver Agent] ─────────────── MCP: get_customer_history, get_order
    │       │ (Resolve candidate vs rejected)
    │       ▼ (Trace: handoff)
    │
    ├─► [Specialist Agents]
    │       ├─► Shipment Specialist ────────── MCP: get_shipment_summary
    │       ├─► Payment/Refund Specialist ──── MCP: get_order_payments, get_payment_timeline, get_refund_timeline
    │       ├─► Order/Catalog Specialist ───── MCP: get_order_items, get_sellers, get_product_context
    │       └─► Policy Specialist ──────────── MCP: get_policy
    │       │ (Trace: tool_result_consumed, handoff)
    │       ▼
    ├─► [Conflict Resolver] ────────────────── Resolve timestamps & order status precedence
    │       │
    │       ▼
    └─► [Verifier Agent] ───────────────────── Verify invariants, schema, consistency
            │ (Trace: verification_completed)
            ▼
    Output JSON & (Trace: case_finalized)
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tool permission | Output/handoff |
| --- | --- | --- | --- | --- |
| **Coordinator** | Case input payload | Tiếp nhận case, lập kế hoạch, phân bổ nhiệm vụ cho các sub-agents, tổng hợp kết luận | Không gọi tool trực tiếp | Phân phối task (`task_assigned`) và nhận kết quả |
| **Entity Resolver** | `candidate_order_ids`, `customer_unique_id_hint`, `claimed_order_id` | Khớp danh tính khách hàng, phân tách order hợp lệ và loại bỏ candidate sai | `get_customer_history`, `get_order` | `resolved_order_ids`, `rejected_candidates`, `customer_context` |
| **Policy Specialist** | `policy_version`, `case_id` | Tải quy định chính sách của sàn, cung cấp bảng luật xử lý, mức hoàn tiền và trách nhiệm | `get_policy` | Policy rules, base actions, standard compensation |
| **Order/Catalog Specialist** | `order_id` | Trích xuất thông tin mặt hàng, giá niêm yết, phí vận chuyển và danh mục sản phẩm | `get_order_items`, `get_sellers`, `get_product_context` | `item_ids`, `seller_ids`, catalog translations |
| **Shipment Specialist** | `order_id` | Đánh giá tiến độ giao hàng, phân định trễ hạn do seller hay đơn vị vận chuyển (logistics) | `get_shipment_summary` | `shipment_verdict`, `late_seller_ids`, `timeline_complete` |
| **Payment/Refund Specialist** | `order_id` | Đối soát dòng tiền: số tiền đã thu, các đợt hoàn tiền, phát hiện thu trùng hoặc lệch tiền | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` | `payment_verdict`, `captured_total`, `refundable_total` |
| **Conflict Resolver** | Dữ liệu từ các specialist | Phát hiện và xử lý mâu thuẫn dữ liệu giữa các nguồn (timeline vận chuyển, trạng thái đơn) | Không gọi tool trực tiếp | `data_conflicts` kèm resolution code |
| **Verifier** | Bản thảo output hoàn chỉnh | Thẩm định các bất biến (invariants), kiểm tra schema JSON, đảm bảo tính nhất quán | Không gọi tool trực tiếp | Phán quyết xác thực (`verification_completed`) |

## 3. Entity resolution và A2A protocol

- **Nguyên tắc định danh**: Khách hàng cung cấp `claimed_order_id` và danh sách `candidate_order_ids`. Hệ thống gọi `get_customer_history` bằng `customer_unique_id_hint` để lấy danh mục đơn hàng chính chủ từ cơ sở dữ liệu.
- **Xếp hạng và loại trừ**:
  - Ứng viên trùng với `claimed_order_id` và nằm trong lịch sử khách hàng được đưa vào `resolved_order_ids` (độ tin cậy: 0.98).
  - Các mã định danh rác hoặc không thuộc quyền sở hữu của khách hàng được phân loại vào `rejected_candidates`.
- **A2A Protocol Envelope**:
  - Giao tiếp giữa các agent thông qua thông điệp có ngữ cảnh đầy đủ, liên kết chặt chẽ bởi `case_id`.
  - Handoff tuần tự: Coordinator -> Entity Resolver -> Specialists -> Conflict Resolver -> Verifier -> Coordinator.
  - Ngăn ngừa vòng lặp: Mỗi agent thực hiện đúng trách nhiệm chuyên môn một lần duy nhất theo pipeline định sẵn; không gọi lại các tác vụ đã hoàn tất.

## 4. Evidence và conflict lifecycle

- **Thu thập và Audit**: Mọi phản hồi từ MCP Gateway đều chứa `evidence_ref` duy nhất do server cấp. Hệ thống ghi nhận ngay lập tức vào trace thông qua sự kiện `tool_result_consumed` với đúng actor tương ứng.
- **Không tái sử dụng chéo case**: Mọi evidence đều gắn kèm `case_id` hiện tại, không sử dụng evidence từ run hoặc case khác để tránh vi phạm Hard Gate.
- **Vòng đời xử lý xung đột (Data Conflict)**:
  - Khi phát hiện mâu thuẫn giữa `shipment_summary` và `order_items` về mốc thời gian giao hàng (`shipping_limit_at`), ưu tiên nguồn từ `shipment_summary` với mã `authoritative_shipment_precedence`.
  - Khi có sự sai khác về `order_status` giữa bản ghi đơn hàng gốc và lịch sử khách hàng, ưu tiên trạng thái đơn hàng của bảng order chính thống.

## 5. Failure and efficiency policy

| Failure | Retry budget | Fallback | Trace event/code |
| --- | ---: | --- | --- |
| **MCP Timeout** | 2 lần (exponential backoff) | Đánh dấu `insufficient_evidence`, giữ nguyên trạng thái an toàn | `MCP_TIMEOUT_FALLBACK` |
| **Entity not found/ambiguous** | 1 lần qua `get_order` thử nghiệm | Đánh dấu `status: not_found`, confidence thấp | `ENTITY_RESOLUTION_AMBIGUOUS` |
| **Source conflict** | 0 (deterministic rule) | Áp dụng chính sách ưu tiên nguồn tin cậy nhất (`authoritative precedence`) | `CONFLICT_RESOLVED` |
| **Tool Execution Error** (ví dụ: timeline trống) | 0 | Coi như không có sự kiện phát sinh, tiếp tục phân tích các nguồn khác | `OPTIONAL_DATA_SKIPPED` |

- **Chiến lược tối ưu quota gọi tool (Efficiency Budget)**:
  - Chỉ gọi tool cho đơn hàng đã được resolve thành công; tuyệt đối không gọi tool cho các `rejected_candidates`.
  - Mỗi tool được gọi tối đa 1 lần duy nhất cho mỗi case (zero duplicate calls).
  - Bọc các tool phụ (`get_refund_timeline`, `get_payment_timeline`) trong khối an toàn để không làm gián đoạn luồng xử lý chính.

## 6. Verification invariants

Trước khi xuất xưởng output, Verifier tiến hành rà soát các ràng buộc bắt buộc:
1. **Schema Compliance**: Tuân thủ 100% JSON Schema của hợp đồng `day09-l3b-output-v2`.
2. **Case ID Integrity**: `output.case_id` phải khớp chính xác với `case.case_id`.
3. **Candidate Separation**: `resolved_order_ids` và `rejected_candidates` phải rời nhau hoàn toàn (disjoint sets).
4. **Evidence Provenance**: Mọi `evidence_ref` trong `evidence_refs` và `claim_assessments` phải xuất phát từ MCP Gateway trong cùng case.
5. **Financial Consistency**:
   - Nếu `recommended_refund_brl == 0`, mảng `refund_lines` phải rỗng.
   - Nếu `recommended_refund_brl > 0`, tổng tiền trong `refund_lines` phải bằng `recommended_refund_brl`.
   - `currency` luôn luôn là `"BRL"`.
6. **Action Consistency**: Hành động trong `resolution_actions` phải tương thích với `case_status` (ví dụ: `action_required` đi kèm với hành động xử lý cụ thể như `refund_freight`, `issue_refund`).

## 7. Reproducibility

- **Mô hình suy luận (LLM Reasoner)**: Qwen 3 8B (`qwen/qwen3-8b`) thông qua OpenRouter API, kết hợp cơ chế deterministic fallback khi API timeout hoặc không có kết nối mạng.
- **Runtime & Environment**: Python 3.11+, MCP SDK, `httpx2`, Pydantic V2.
- **Concurrency**: Thực thi tuần tự theo từng case theo vòng lặp Asyncio chuẩn, đảm bảo thứ tự trace sự kiện determinism.
- **Config & Secrets**: Toàn bộ cấu hình đọc từ biến môi trường qua `.env` (`COMPETITION_API_URL`, `COMPETITION_TEAM_API_KEY`, `MCP_ENDPOINT`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`). Tuyệt đối không ghi thông tin nhạy cảm vào trace hoặc file nộp bài.
- **Lệnh thực thi**:
  ```powershell
  day09 run
  day09 validate
  day09 package --output dist/submission.zip
  ```
