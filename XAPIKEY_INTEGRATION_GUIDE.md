# X-Api-Key Authentication - Integration Guide

## 📋 Overview

Hệ thống giờ đây hỗ trợ xác thực X-Api-Key bằng cách tích hợp trực tiếp với `utils/permission.py` và MongoDB.

**Flow**:
```
X-Api-Key Header
    ↓
[1] Verify API key trong MongoDB (instance_data_danh_sach_api_key)
    ├─ Check api_key khớp
    ├─ Check is_active = true
    ├─ Check is_deleted ≠ true
    └─ Check ngay_het_han_token chưa quá hạn
    ↓
[2] Get username từ API key record
    ↓
[3] Get user info từ nhan_vien collection
    ├─ Lấy _id, email, company_code
    ├─ Lấy don_vi_cong_tac (department)
    ├─ Lấy vi_tri_cong_viec (position)
    └─ Lấy vai_tro (roles)
    ↓
[4] Get accessible ma_chuc_nang từ danh_sach_phan_quyen_chuc_nang
    ├─ Match by vai_tro (roles)
    ├─ Match by don_vi_cong_tac (department)
    ├─ Match by phong_ban_phu_trach (phòng ban)
    └─ Match by danh_sach_nguoi_dung (individual user)
    ↓
[5] Map ma_chuc_nang → instance_name (sys_conf_view)
    ↓
[6] Return UserPermissionContext
    ├─ user_id
    ├─ username
    ├─ accessible_instance_names
    ├─ accessible_instances (map tới ma_chuc_nang)
    └─ company_code
```

---

## 🔑 API Key Format

**Format của bạn**: `818eccbf414d45918ec7e196de10737d`

Hệ thống không yêu cầu prefix cụ thể. Hỗ trợ:
- ✅ `818eccbf414d45918ec7e196de10737d` (UUID)
- ✅ `sk_prod_abc123...` (với prefix)
- ✅ Bất kỳ string nào được lưu trong `instance_data_danh_sach_api_key`

---

## 📊 MongoDB Collection: `instance_data_danh_sach_api_key`

Hãy lưu API key vào MongoDB với schema sau:

```json
{
  "_id": ObjectId("..."),
  "api_key": "818eccbf414d45918ec7e196de10737d",
  "ten_dang_nhap": "username_of_user",
  "ho_va_ten": "Full Name",
  "email": "user@example.com",
  "company_code": "HITC",
  "is_active": true,
  "is_deleted": false,
  "ngay_het_han_token": null,  // hoặc ISO date nếu muốn expiry
  "ngay_tao": "2024-01-20T10:30:00Z",
  "created_by": "admin"
}
```

---

## 🧪 Test API Key

### Step 1: Insert API Key vào MongoDB

```javascript
// MongoDB shell
use your_database;
db.instance_data_danh_sach_api_key.insertOne({
  api_key: "818eccbf414d45918ec7e196de10737d",
  ten_dang_nhap: "john_doe",
  ho_va_ten: "John Doe",
  email: "john@example.com",
  company_code: "HITC",
  is_active: true,
  is_deleted: false,
  ngay_het_han_token: null,
  ngay_tao: new Date(),
  created_by: "system"
});
```

### Step 2: Lấy thông tin user (ví dụ username = john_doe)

Chắc chắn user `john_doe` tồn tại trong `instance_data_thong_tin_nhan_vien`:

```javascript
db.instance_data_thong_tin_nhan_vien.insertOne({
  ten_dang_nhap: "john_doe",
  email: "john@example.com",
  company_code: "HITC",
  don_vi_cong_tac: {
    value: "HR",
    option: { code: "HR" }
  },
  path_don_vi_cong_tac: "/HR",
  vi_tri_cong_viec: "Manager",
  vai_tro: [{ value: "HR_MANAGER" }],
  is_deleted: false
});
```

### Step 3: Setup Permissions

Đảm bảo user có permissions trong `instance_data_danh_sach_phan_quyen_chuc_nang`:

```javascript
db.instance_data_danh_sach_phan_quyen_chuc_nang.insertOne({
  ma_chuc_nang: "SALARY_READ",
  vai_tro: [{ value: "HR_MANAGER" }],
  instance_name: "HR_System",
  is_active: true,
  is_deleted: false
});

db.instance_data_danh_sach_phan_quyen_chuc_nang.insertOne({
  ma_chuc_nang: "SALARY_WRITE",
  vai_tro: [{ value: "HR_MANAGER" }],
  instance_name: "HR_System",
  is_active: true,
  is_deleted: false
});
```

### Step 4: Test với curl

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the salary for Ma01?",
    "session_id": "test-session-123"
  }'
```

### Step 5: Check Response

**Success** (✅ 200 OK):
```json
{
  "message": "...",
  "session_id": "test-session-123",
  "user_id": "...",
  "data": {...}
}
```

**Failure** (❌ 401):
```json
{
  "detail": "API Key không hợp lệ hoặc đã bị vô hiệu hoá"
}
```

---

## 🐛 Troubleshooting

### 401: "API Key không hợp lệ hoặc đã bị vô hiệu hoá"

**Kiểm tra**:
1. API key có tồn tại trong MongoDB?
   ```javascript
   db.instance_data_danh_sach_api_key.findOne({
     api_key: "818eccbf414d45918ec7e196de10737d"
   });
   ```

2. `is_active` = true?
3. `is_deleted` ≠ true?
4. `ngay_het_han_token` chưa quá hạn?

### 401: "User not found"

**Kiểm tra**:
1. Username từ API key record có tồn tại trong `instance_data_thong_tin_nhan_vien`?
   ```javascript
   db.instance_data_thong_tin_nhan_vien.findOne({
     ten_dang_nhap: "john_doe"
   });
   ```

2. `is_deleted` ≠ true?

### 500: "Authentication service error"

**Kiểm tra**:
1. MongoDB connection OK?
2. Collections có tồn tại?
3. Check logs chi tiết:
   ```python
   import logging
   logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
   ```

---

## 📝 Debug Logging

Enable debug để xem chi tiết:

```python
# app/main.py
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
logging.getLogger("app.middleware.auth_middleware").setLevel(logging.DEBUG)
logging.getLogger("utils.permission").setLevel(logging.DEBUG)
```

**Expected logs**:
```
DEBUG - Verifying API key: 818eccbf414d45...
DEBUG - API key verified for user: john_doe
DEBUG - User john_doe has 2 functions
INFO  - User authenticated via API key: john_doe (...)
```

---

## 🔄 HTTP Headers Supported

### Bearer Token (JWT từ Keycloak)
```
Authorization: Bearer eyJhbGc...
```

### X-Api-Key
```
X-Api-Key: 818eccbf414d45918ec7e196de10737d
```

**Middleware sẽ thử**:
1. Nếu có `Authorization: Bearer ...` → dùng JWT
2. Else nếu có `X-Api-Key: ...` → dùng API key
3. Else → return 401

---

## 📊 Data Flow

```
Client Request
  ├─ Header: X-Api-Key: 818eccbf414d45918ec7e196de10737d
  └─ Body: {message: "...", session_id: "..."}
    ↓
AuthenticationMiddleware
  ├─ Extract X-Api-Key
  ├─ Call: perm_svc._verify_api_key(api_key)
  │   └─ Query: instance_data_danh_sach_api_key
  │       └─ Return: username = "john_doe"
  │
  ├─ Call: perm_svc._get_nhan_vien(username)
  │   └─ Query: instance_data_thong_tin_nhan_vien
  │       └─ Return: {_id, email, company_code, ...}
  │
  ├─ Call: perm_svc._get_accessible_chuc_nang(nv)
  │   └─ Query: instance_data_danh_sach_phan_quyen_chuc_nang
  │       └─ Return: [ma_chuc_nang_1, ma_chuc_nang_2, ...]
  │
  ├─ Call: perm_svc._get_accessible_instances(chuc_nang_list)
  │   └─ Query: instance_data_sys_conf_view
  │       └─ Return: {instance_name: [ma_chuc_nang, ...], ...}
  │
  └─ Inject: request.state.user = UserPermissionContext(...)
    ↓
Route Handler / AgentOS
  └─ Access: request.state.user (all data available!)
    ↓
Workflow
  └─ Save: session_store.save_context(session_id, user_id, ...)
    ↓
MCP Tools
  └─ Retrieve: session_store.get_context(session_id)
     └─ Validate: permissions and return filtered data
```

---

## ✅ Verification Checklist

- [ ] API key `818eccbf414d45918ec7e196de10737d` inserted in MongoDB
- [ ] User `john_doe` exists in `instance_data_thong_tin_nhan_vien`
- [ ] User has `vai_tro` (role) assigned
- [ ] Permissions exist in `instance_data_danh_sach_phan_quyen_chuc_nang` for the role
- [ ] Test request returns 200 OK (not 401)
- [ ] User context available in logs
- [ ] Session context saved properly
- [ ] MCP tools can access user context

---

## 📞 Support

Nếu gặp vấn đề, hãy cung cấp:
1. API key format bạn dùng
2. MongoDB collection schema
3. Full error log từ server
4. Username của test user
