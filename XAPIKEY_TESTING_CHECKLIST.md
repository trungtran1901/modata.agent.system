# 🚀 X-Api-Key Testing Checklist

## Pre-Testing Setup

### Step 1: Read Documentation ✅
- [x] Understand the architecture
- [x] Read `XAPIKEY_MONGODB_SETUP.md`
- [ ] Prepare MongoDB queries

### Step 2: MongoDB Setup (Your Task)
- [ ] Connect to MongoDB
- [ ] Insert API key record
- [ ] Insert/verify user record
- [ ] Insert/verify permissions
- [ ] Insert/verify instance mappings
- [ ] Run verification queries

---

## Testing Scenarios

### Scenario 1: Valid API Key

**Setup**:
```javascript
// API key record
db.instance_data_danh_sach_api_key.insertOne({
  api_key: "818eccbf414d45918ec7e196de10737d",
  ten_dang_nhap: "test_user",
  ho_va_ten: "Test User",
  email: "test@example.com",
  company_code: "HITC",
  is_active: true,      // ← IMPORTANT
  is_deleted: false,    // ← IMPORTANT
  ngay_het_han_token: null
})
```

**Test**:
```bash
curl -v -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test query",
    "session_id": "test-123"
  }'
```

**Expected**:
- [ ] Status: 200 OK
- [ ] Response contains: message, session_id, user_id, data
- [ ] Logs show: "User authenticated via API key: test_user"

**Checklist**:
- [ ] API key inserted
- [ ] User record exists in nhan_vien
- [ ] User has vai_tro assigned
- [ ] Permissions exist for vai_tro
- [ ] Instance mappings exist
- [ ] Test passes

---

### Scenario 2: Invalid API Key

**Test**:
```bash
curl -v -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: invalid_key" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test query",
    "session_id": "test-123"
  }'
```

**Expected**:
- [ ] Status: 401 Unauthorized
- [ ] Response: `{"detail": "API Key không hợp lệ hoặc đã bị vô hiệu hoá"}`
- [ ] Logs show: "Invalid API key lookup"

**Checklist**:
- [ ] Non-existent key returns 401 ✅

---

### Scenario 3: Disabled API Key

**Setup**:
```javascript
// Set is_active to false
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { is_active: false } }
)
```

**Test**: (same as Scenario 1)

**Expected**:
- [ ] Status: 401 Unauthorized
- [ ] Response: `{"detail": "API Key không hợp lệ hoặc đã bị vô hiệu hoá"}`

**Cleanup**:
```javascript
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { is_active: true } }
)
```

**Checklist**:
- [ ] Disabled key returns 401 ✅

---

### Scenario 4: Deleted API Key

**Setup**:
```javascript
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { is_deleted: true } }
)
```

**Test**: (same as Scenario 1)

**Expected**:
- [ ] Status: 401 Unauthorized

**Cleanup**:
```javascript
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { is_deleted: false } }
)
```

**Checklist**:
- [ ] Deleted key returns 401 ✅

---

### Scenario 5: Expired API Key

**Setup**:
```javascript
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { ngay_het_han_token: new Date("2020-01-01") } }
)
```

**Test**: (same as Scenario 1)

**Expected**:
- [ ] Status: 401 Unauthorized
- [ ] Response: `{"detail": "API Key đã hết hạn"}`

**Cleanup**:
```javascript
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { ngay_het_han_token: null } }
)
```

**Checklist**:
- [ ] Expired key returns 401 ✅

---

### Scenario 6: User Not Found

**Setup**:
```javascript
// Update API key to reference non-existent user
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { ten_dang_nhap: "nonexistent_user" } }
)
```

**Test**: (same as Scenario 1)

**Expected**:
- [ ] Status: 401 Unauthorized
- [ ] Response: `{"detail": "User not found"}`

**Cleanup**:
```javascript
db.instance_data_danh_sach_api_key.updateOne(
  { api_key: "818eccbf414d45918ec7e196de10737d" },
  { $set: { ten_dang_nhap: "test_user" } }
)
```

**Checklist**:
- [ ] Non-existent user returns 401 ✅

---

### Scenario 7: Bearer Token Still Works

**Setup**: Get a valid JWT token from Keycloak

**Test**:
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test with Bearer",
    "session_id": "test-456"
  }'
```

**Expected**:
- [ ] Status: 200 OK (if user has permissions)
- [ ] Bearer token still works after X-Api-Key implementation

**Checklist**:
- [ ] Bearer token not broken ✅

---

### Scenario 8: User Context Passed Through

**Setup**: Valid API key from Scenario 1

**Test**: Enable debug logging and check logs

```python
# In app/main.py
import logging
logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
logging.getLogger("app.middleware.auth_middleware").setLevel(logging.DEBUG)
```

**Expected in Logs**:
```
DEBUG - Verifying API key: 818eccbf414d45...
DEBUG - API key verified for user: test_user
DEBUG - User test_user has X functions
DEBUG - Built user context from API key: test_user_id
INFO  - User authenticated via API key: test_user (test_user_id)
```

**Checklist**:
- [ ] Logs show correct flow ✅
- [ ] user_id is correct ✅
- [ ] Function count matches ✅

---

### Scenario 9: No Authentication (Excluded Route)

**Test**:
```bash
curl -X GET "http://localhost:8000/health"
```

**Expected**:
- [ ] Status: 200 OK
- [ ] No authentication required
- [ ] Logs show: "Skipping auth for excluded route"

**Checklist**:
- [ ] Excluded routes work ✅

---

## Integration Tests

### Test 1: End-to-End with MCP Tools

**Test**:
```bash
# Call with X-Api-Key
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data for Ma01",
    "session_id": "integration-test-1"
  }'
```

**Verify**:
- [ ] Response contains employee data
- [ ] MCP tool called successfully
- [ ] Permission validation worked
- [ ] Data filtered correctly

**Checklist**:
- [ ] MCP tool receives context ✅
- [ ] Permissions validated ✅
- [ ] Data returned correctly ✅

---

### Test 2: Session Context Saved

**Test**: Run Scenario 1 test and check session store

```python
# In Python shell or test script
from workflow.session import session_store

context = session_store.get_context("test-123")
print(context)
# Should show: {user_id: "...", username: "test_user", ...}
```

**Expected**:
- [ ] Context exists in session store
- [ ] user_id matches API key user
- [ ] Permissions are present
- [ ] company_code is correct

**Checklist**:
- [ ] Session context saved ✅
- [ ] MCP tools can retrieve it ✅

---

## Performance Tests

### Test 1: API Key Lookup Speed

**Test**: Measure response time

```bash
time curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -d '{...}'
```

**Expected**:
- [ ] Response time < 500ms
- [ ] Acceptable for API calls

---

### Test 2: Multiple Concurrent Requests

**Test**:
```bash
# Using ab (Apache Bench) or similar
ab -n 100 -c 10 \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -p data.json \
  http://localhost:8000/teams/hrm-team/runs
```

**Expected**:
- [ ] All requests succeed
- [ ] No connection errors
- [ ] Response times consistent

---

## Final Checklist

### Functionality
- [ ] Valid API key works
- [ ] Invalid API key rejected
- [ ] Disabled key rejected
- [ ] Expired key rejected
- [ ] User not found rejected
- [ ] Bearer token still works
- [ ] Excluded routes work
- [ ] Debug logs show correct flow

### Integration
- [ ] User context passed to MCP tools
- [ ] Session context saved
- [ ] Permissions validated
- [ ] Data filtered correctly
- [ ] End-to-end flow working

### Performance
- [ ] Response time acceptable
- [ ] Concurrent requests handled
- [ ] No memory leaks
- [ ] Logs manageable

### Security
- [ ] API key validation secure
- [ ] User validation secure
- [ ] Permission validation secure
- [ ] Error messages don't leak info
- [ ] Expiry date checked
- [ ] Active/deleted flags checked

### Documentation
- [ ] Setup guide clear
- [ ] MongoDB queries work
- [ ] Troubleshooting guide helps
- [ ] Quick reference available
- [ ] Examples provided

---

## Success Criteria

✅ **All tests pass** = Implementation successful!

```
Scenarios passed: 9/9
Integration tests: 2/2
Performance tests: 2/2
Final checklist: COMPLETE
```

---

## Next Steps After Testing

1. ✅ All tests pass
2. ✅ Fix any issues found
3. ✅ Deploy to staging
4. ✅ Run staging tests
5. ✅ Deploy to production
6. ✅ Monitor for errors

---

## Support

If tests fail:
1. Check `XAPIKEY_MONGODB_SETUP.md` for MongoDB setup
2. Check `XAPIKEY_INTEGRATION_GUIDE.md` for debugging
3. Enable debug logging
4. Check MongoDB data with verification queries
5. Review error messages in logs

Good luck! 🚀
