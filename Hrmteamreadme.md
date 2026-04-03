# HRM Team — Hướng dẫn tích hợp

## Tổng quan kiến trúc

```
Client (Bearer JWT)
    │
    ▼
modata-agent :8000
    ├── POST /chat            ← General agents (agents.py, không đổi)
    ├── POST /hrm/chat        ← HRM Team      (hrm_team.py)   ← MỚI
    ├── GET  /hrm/holidays    ← Direct query  (hrm_routes.py) ← MỚI
    ├── GET  /hrm/leave-types
    └── GET  /hrm/weekly-off-rules
         │
         ▼ session_id → Redis/PG (permission context)
         │
    modata-mcp :8001/sse
         ├── data_*     (data_server.py)       — không đổi
         ├── analytics_* (analytics_server.py) — không đổi
         ├── tools_*    (tools_server.py)       — không đổi
         ├── mail_*     (mail_server.py)        — không đổi
         ├── docs_*     (docs_server.py)        — không đổi
         ├── admin_*    (admin_server.py)        — không đổi
         └── hrm_*      (hrm_server.py)         ← MỚI (8 tools)
              │
              ▼
         MongoDB
              ├── instance_data_thong_tin_nhan_vien
              ├── instance_data_ngay_nghi_le
              ├── instance_data_ngay_nghi_tuan
              └── instance_data_danh_sach_loai_nghi_phep
```

## Files cần tích hợp

### Project modata-mcp

| File | Hành động | Nội dung |
|------|-----------|----------|
| `mcp_servers/hrm_server.py` | **Thêm mới** | 8 HRM tools với prefix `hrm_` |
| `mcp_servers/gateway.py`    | **Thay thế** | Thêm mount `hrm_mcp` prefix `"hrm"` |

### Project modata-agent

| File | Hành động | Nội dung |
|------|-----------|----------|
| `workflow/hrm_team.py`         | **Thêm mới** | HRM Team: 2 agents + chat bridge |
| `app/api/routes/hrm_routes.py` | **Thêm mới** | API endpoints `/hrm/*` |
| `app/main.py`                  | **Thay thế** | Include `hrm_router` |

---

## HRM Tools (prefix: `hrm_`)

| Tool | Collection | Mô tả |
|------|-----------|-------|
| `hrm_get_employee_info` | thong_tin_nhan_vien | Thông tin 1 NV theo username/tên |
| `hrm_search_employees` | thong_tin_nhan_vien | Tìm kiếm full-text (tên, email, SĐT) |
| `hrm_list_employees` | thong_tin_nhan_vien | Danh sách NV theo đơn vị/trạng thái |
| `hrm_get_holidays` | ngay_nghi_le | Ngày nghỉ lễ theo năm hoặc khoảng ngày |
| `hrm_get_weekly_off_rules` | ngay_nghi_tuan | Quy định ngày nghỉ tuần |
| `hrm_get_leave_types` | danh_sach_loai_nghi_phep | Loại nghỉ phép + số ngày tối đa/năm |
| `hrm_check_working_schedule` | ngay_nghi_le + ngay_nghi_tuan | Kiểm tra ngày có phải ngày làm việc |
| `hrm_get_leave_policy_summary` | Cả 3 collections | Tổng hợp toàn bộ chính sách nghỉ |

---

## HRM Team Agents

### Employee Agent (`hrm-employee-agent`)
**Chuyên môn**: Thông tin nhân viên

| Query | Xử lý |
|-------|-------|
| "thông tin của tôi" | `hrm_get_employee_info(sid, username)` |
| "tìm nhân viên Nguyễn Văn A" | `hrm_get_employee_info(sid, "Nguyễn Văn A")` |
| "nhân viên tên Hùng" | `hrm_search_employees(sid, "Hùng")` |
| "danh sách phòng Kế toán" | `hrm_list_employees(sid, don_vi_code="Kế toán")` |
| "A làm bao lâu rồi?" | `get_employee_info` → lấy ngày vào → `tools_calculate_service_time` |

### Leave Info Agent (`hrm-leave-info-agent`)
**Chuyên môn**: Quy định nghỉ phép, ngày nghỉ lễ

| Query | Xử lý |
|-------|-------|
| "ngày nghỉ lễ năm 2025" | `hrm_get_holidays(sid, year=2025)` |
| "tháng 4 nghỉ những ngày nào" | `hrm_get_holidays(sid, from_date=..., to_date=...)` |
| "nghỉ mấy ngày trong tuần" | `hrm_get_weekly_off_rules(sid)` |
| "loại nghỉ phép nào?" | `hrm_get_leave_types(sid)` |
| "02/09 có phải ngày làm việc?" | `hrm_check_working_schedule(sid, "2025-09-02")` |
| "chính sách nghỉ phép" | `hrm_get_leave_policy_summary(sid)` |

---

## Luồng permission (giữ nguyên pattern hiện tại)

```
POST /hrm/chat
    │
    ▼
hrm_routes.get_user()
    → PermissionService.build_context(bearer)
    → Keycloak verify JWT → username, roles
    → MongoDB: thong_tin_nhan_vien → don_vi, path
    → MongoDB: phan_quyen → ma_chuc_nang list
    → MongoDB: sys_conf_view → {instance_name: [ma_chuc_nang]}
    → UserPermissionContext
    │
    ▼
chat_with_hrm_team(query, user, session_id, history)
    │
    ├── session_store.save_context(session_id, accessible)
    │     → Redis: SADD perm:{sid}:instances {instance_name, ...}
    │     → Redis: SADD perm:{sid}:ma:{inst} {ma_chuc_nang, ...}
    │     → PG:    UPDATE rag_sessions SET accessible_context = {dict}
    │
    ├── augmented_query = "[session_id:xxx] [username:xxx] ..."
    │
    └── HRM Team.arun(augmented_query)
            │
            ├── Employee Agent → hrm_get_employee_info(session_id, ...)
            │       │
            │       └── hrm_server.py
            │             → get_session_context(session_id)
            │               → Redis SISMEMBER O(1)
            │               → fallback PG nếu Redis miss
            │             → can_access("thong_tin_nhan_vien") → True/False
            │             → MongoDB query → _flatten_nhan_vien() → result
            │
            └── Leave Info Agent → hrm_get_holidays(session_id, ...)
                    │
                    └── hrm_server.py
                          → _require_valid_session(session_id)
                          → MongoDB query → _flatten_ngay_nghi_le() → result
```

---

## API Usage

### Chat với HRM Team

```bash
curl -X POST http://localhost:8000/hrm/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Năm 2025 có những ngày nghỉ lễ nào?",
    "session_id": "sess-abc123"
  }'
```

**Response:**
```json
{
  "session_id": "sess-abc123",
  "answer": "Năm 2025, HITC có các ngày nghỉ lễ sau:\n📅 Tết Dương lịch: 01/01/2025 (1 ngày)\n...",
  "team": "HRM Team",
  "agents": ["Employee Agent", "Leave Info Agent"],
  "sources": [],
  "metrics": {"total_duration": 2.35, "team": "hrm"}
}
```

### Direct endpoints (không qua LLM, nhanh hơn)

```bash
# Ngày nghỉ lễ năm 2025
GET http://localhost:8000/hrm/holidays?year=2025

# Nghỉ tháng 4-5/2025
GET http://localhost:8000/hrm/holidays?from_date=2025-04-01&to_date=2025-05-31

# Loại nghỉ phép
GET http://localhost:8000/hrm/leave-types

# Quy định nghỉ tuần
GET http://localhost:8000/hrm/weekly-off-rules
```

---

## Thêm Agent mới vào HRM Team

### Bước 1: Thêm tools vào `hrm_server.py`
```python
@mcp.tool()
def get_attendance_summary(session_id: str, username: str, month: str) -> str:
    """Tổng hợp chấm công tháng."""
    err = _require_valid_session(session_id, "lich_su_cham_cong_tong_hop_cong")
    if err:
        return err
    # ... query MongoDB
```

### Bước 2: Thêm agent trong `hrm_team.py`
```python
ATTENDANCE_AGENT_PROMPT = """
Bạn là Attendance Agent — chuyên về chấm công, giờ vào ra.
COLLECTION: lich_su_cham_cong_tong_hop_cong
TOOLS: hrm_get_attendance_summary, tools_get_current_time
"""

def _build_attendance_agent(mcp_tools: MCPTools) -> Agent:
    return Agent(
        id="hrm-attendance-agent",
        name="Attendance Agent",
        role="Chuyên gia chấm công",
        model=_make_model(max_tokens=512, temperature=0.3),
        description=ATTENDANCE_AGENT_PROMPT,
        tools=[mcp_tools],
    )
```

### Bước 3: Thêm vào `build_hrm_team()`
```python
def build_hrm_team() -> Team:
    mcp_tools = MCPTools(url=settings.MCP_GATEWAY_URL, transport="sse")
    return Team(
        ...
        members=[
            _build_employee_agent(mcp_tools),
            _build_leave_info_agent(mcp_tools),
            _build_attendance_agent(mcp_tools),   # ← Thêm
        ],
        ...
    )
```

---

## Không cần thay đổi

- `utils/session.py` — SessionContext, get_session_context() dùng chung
- `utils/perm_store.py` — Redis permission store dùng chung
- `workflow/session.py` — SessionStore (PG) dùng chung
- `utils/permission.py` — PermissionService dùng chung
- `workflow/agents.py` — General agents không ảnh hưởng
- `app/api/routes/routes.py` — /chat không ảnh hưởng