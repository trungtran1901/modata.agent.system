# X-Api-Key Authentication - Implementation Summary

## ✅ What Was Implemented

Tôi đã tích hợp X-Api-Key authentication với format của bạn (`818eccbf414d45918ec7e196de10737d`) vào hệ thống.

### Implementation Details

**File**: `app/middleware/permission.py`

**Function**: `build_user_context_from_api_key(api_key: str)`

**Flow**:
```
API Key Header (818eccbf414d45918ec7e196de10737d)
    ↓
Verify in MongoDB (instance_data_danh_sach_api_key)
    ↓
Get username (ten_dang_nhap)
    ↓
Get user info from instance_data_thong_tin_nhan_vien
    ↓
Get permissions from instance_data_danh_sach_phan_quyen_chuc_nang
    ↓
Map to instances from instance_data_sys_conf_view
    ↓
Return UserPermissionContext
```

---

## 🚀 Quick Start

### 1. Insert API Key into MongoDB

```bash
# Use MongoDB Compass or mongosh

use your_database

db.instance_data_danh_sach_api_key.insertOne({
  api_key: "818eccbf414d45918ec7e196de10737d",
  ten_dang_nhap: "john_doe",          // Username
  ho_va_ten: "John Doe",               // Full name
  email: "john@example.com",
  company_code: "HITC",
  is_active: true,                     // IMPORTANT!
  is_deleted: false,                   // IMPORTANT!
  ngay_het_han_token: null,            // null = no expiry
  ngay_tao: new Date(),
  created_by: "system"
})
```

### 2. Test

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the salary for Ma01?",
    "session_id": "test-123"
  }'
```

---

## 📋 Required MongoDB Collections

Your API key needs user data in these collections:

### 1. `instance_data_danh_sach_api_key` (API Key Store)
```javascript
{
  api_key: "818eccbf414d45918ec7e196de10737d",
  ten_dang_nhap: "john_doe",
  ho_va_ten: "John Doe",
  email: "john@example.com",
  company_code: "HITC",
  is_active: true,
  is_deleted: false,
  ngay_het_han_token: null
}
```

### 2. `instance_data_thong_tin_nhan_vien` (Employee Info)
```javascript
{
  ten_dang_nhap: "john_doe",
  email: "john@example.com",
  company_code: "HITC",
  don_vi_cong_tac: { value: "HR", option: { code: "HR" } },
  path_don_vi_cong_tac: "/HR",
  vi_tri_cong_viec: "Manager",
  vai_tro: [{ value: "HR_MANAGER" }],
  is_deleted: false
}
```

### 3. `instance_data_danh_sach_phan_quyen_chuc_nang` (Permissions)
```javascript
{
  ma_chuc_nang: "SALARY_READ",
  vai_tro: [{ value: "HR_MANAGER" }],
  instance_name: "HR_System",
  is_active: true,
  is_deleted: false
}
```

### 4. `instance_data_sys_conf_view` (Function to Instance Mapping)
```javascript
{
  ma_chuc_nang: "SALARY_READ",
  instance_name: "HR_System",
  is_active: true,
  is_deleted: false
}
```

---

## 🔑 Key Features

✅ **Format Support**: Supports any format (UUID, prefixed, etc.)
✅ **MongoDB Integration**: Direct integration with your collections
✅ **Permission-Based**: Automatic permission resolution based on:
   - User role (vai_tro)
   - Department (don_vi_cong_tac)
   - Position (vi_tri_cong_viec)
   - Individual permissions

✅ **Expiry Support**: Can set `ngay_het_han_token` for API key expiry
✅ **Active/Deleted Flags**: Supports is_active and is_deleted checks

---

## 📝 Test Scenarios

### Scenario 1: Valid API Key
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -d '{"message": "test", "session_id": "abc123"}'
# Result: 200 OK ✅
```

### Scenario 2: Invalid API Key
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: invalid_key" \
  -d '{"message": "test", "session_id": "abc123"}'
# Result: 401 Unauthorized ❌
# Message: "API Key không hợp lệ hoặc đã bị vô hiệu hoá"
```

### Scenario 3: API Key Disabled
```javascript
// Set is_deleted: true
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { is_deleted: true } }
)
```
```bash
# Result: 401 Unauthorized ❌
# Message: "API Key không hợp lệ hoặc đã bị vô hiệu hoá"
```

### Scenario 4: API Key Expired
```javascript
// Set ngay_het_han_token to past date
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { ngay_het_han_token: new Date("2023-01-01") } }
)
```
```bash
# Result: 401 Unauthorized ❌
# Message: "API Key đã hết hạn"
```

---

## 🐛 Debugging

### Enable Debug Logs

```python
# In app/main.py or config
import logging

logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
logging.getLogger("app.middleware.auth_middleware").setLevel(logging.DEBUG)
logging.getLogger("utils.permission").setLevel(logging.DEBUG)
```

### Check API Key in MongoDB

```javascript
// Find your API key
db.instance_data_danh_sach_api_key.findOne({
  api_key: "818eccbf414d45918ec7e196de10737d"
})

// Check fields
// - is_active should be true
// - is_deleted should be false or not exist
// - ngay_het_han_token should be null or future date
// - ten_dang_nhap should match an existing user
```

### Trace the Flow

1. **Check API Key Exists**:
   ```javascript
   db.instance_data_danh_sach_api_key.findOne({api_key: "..."})
   ```

2. **Check User Exists** (use ten_dang_nhap from above):
   ```javascript
   db.instance_data_thong_tin_nhan_vien.findOne({
     ten_dang_nhap: "john_doe"
   })
   ```

3. **Check User Permissions** (use vai_tro from above):
   ```javascript
   db.instance_data_danh_sach_phan_quyen_chuc_nang.find({
     vai_tro: { $elemMatch: { value: "HR_MANAGER" } }
   })
   ```

4. **Check Instance Mapping**:
   ```javascript
   db.instance_data_sys_conf_view.find({
     ma_chuc_nang: "SALARY_READ"
   })
   ```

---

## 🔄 Complete Integration

### Headers Supported

```
# Bearer Token (JWT)
Authorization: Bearer eyJhbGc...

# X-Api-Key
X-Api-Key: 818eccbf414d45918ec7e196de10737d

# Middleware tries in order:
# 1. Bearer token if present
# 2. X-Api-Key if present
# 3. Return 401 if neither
```

### User Context Injected

After successful authentication:

```python
request.state.user = UserPermissionContext(
    user_id = "...",                    # From nhan_vien._id
    username = "john_doe",              # From API key ten_dang_nhap
    accessible_instance_names = [...],  # Instance names
    accessible_instances = {...},       # {instance: [ma_chuc_nang]}
    company_code = "HITC"
)
```

### Available in All Downstream

- ✅ Route handlers: `request.state.user`
- ✅ AgentOS: `user_id` parameter
- ✅ Workflow: `UserPermissionContext`
- ✅ MCP Tools: via `session_store.get_context()`

---

## 📦 Files Modified/Created

| File | Status | Purpose |
|------|--------|---------|
| `app/middleware/permission.py` | ✅ Updated | Integrated with utils/permission.py |
| `XAPIKEY_INTEGRATION_GUIDE.md` | ✅ Created | Full integration guide |
| `HITC_AUTH_QUICK_REFERENCE.md` | ✅ Updated | Quick reference with X-Api-Key |

---

## ✅ Next Steps

1. **Setup MongoDB**
   - [ ] Insert API key record
   - [ ] Ensure user exists in nhan_vien
   - [ ] Verify permissions configured

2. **Test**
   - [ ] Test with valid API key
   - [ ] Test with invalid API key
   - [ ] Check logs for details
   - [ ] Verify user context in requests

3. **Deploy**
   - [ ] Enable on staging
   - [ ] Run full integration test
   - [ ] Deploy to production

---

## 📞 Questions?

Refer to:
- `XAPIKEY_INTEGRATION_GUIDE.md` - Full integration steps
- `HITC_AGENTOS_AUTHENTICATION_SUMMARY.md` - Architecture overview
- `HITC_AUTH_QUICK_REFERENCE.md` - Quick reference
