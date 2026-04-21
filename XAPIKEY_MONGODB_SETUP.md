# X-Api-Key Setup - MongoDB Queries

## 📊 Complete MongoDB Setup for Your API Key

Use these queries to setup your API key in MongoDB.

---

## Step 1: Insert API Key Record

```javascript
// mongosh or MongoDB Compass

use your_database;

// Insert API key
db.instance_data_danh_sach_api_key.insertOne({
  api_key: "818eccbf414d45918ec7e196de10737d",
  ten_dang_nhap: "john_doe",              // Must match a user in nhan_vien
  ho_va_ten: "John Doe",
  email: "john@example.com",
  company_code: "HITC",
  is_active: true,                        // REQUIRED: true
  is_deleted: false,                      // REQUIRED: false or missing
  ngay_het_han_token: null,               // null = no expiry, or ISO date
  ngay_tao: new Date(),
  created_by: "system"
});

// Verify inserted
db.instance_data_danh_sach_api_key.findOne({
  api_key: "818eccbf414d45918ec7e196de10737d"
});
```

---

## Step 2: Ensure User Exists in Employee Collection

```javascript
// Check if user already exists
db.instance_data_thong_tin_nhan_vien.findOne({
  ten_dang_nhap: "john_doe"
});

// If not, insert user
db.instance_data_thong_tin_nhan_vien.insertOne({
  _id: ObjectId(),
  ten_dang_nhap: "john_doe",              // MUST MATCH API key's ten_dang_nhap
  ho_va_ten: "John Doe",
  email: "john@example.com",
  company_code: "HITC",
  
  // Department (required)
  don_vi_cong_tac: {
    value: "HR_DEPT",
    option: {
      code: "HR_DEPT",
      label: "Human Resources"
    }
  },
  path_don_vi_cong_tac: "/HITC/HR_DEPT",
  
  // Position
  vi_tri_cong_viec: "Manager",
  
  // Roles (required for permission matching)
  vai_tro: [
    { value: "HR_MANAGER", label: "HR Manager" },
    { value: "ADMIN", label: "Administrator" }
  ],
  
  // Status
  is_deleted: false,
  is_active: true,
  
  // Audit
  ngay_tao: new Date(),
  created_by: "system"
});

// Verify
db.instance_data_thong_tin_nhan_vien.findOne({
  ten_dang_nhap: "john_doe"
});
```

---

## Step 3: Setup Permissions

```javascript
// Create permissions for HR Manager role

// Permission 1: SALARY_READ
db.instance_data_danh_sach_phan_quyen_chuc_nang.insertOne({
  _id: ObjectId(),
  ma_chuc_nang: "SALARY_READ",
  ten_chuc_nang: "Read Salary",
  vai_tro: [
    { value: "HR_MANAGER", label: "HR Manager" }
  ],
  don_vi_cong_tac: [],
  phong_ban_phu_trach: [],
  danh_sach_nguoi_dung: [],
  instance_name: "HR_System",           // Links to instance
  is_active: true,
  is_deleted: false,
  ngay_tao: new Date()
});

// Permission 2: SALARY_WRITE
db.instance_data_danh_sach_phan_quyen_chuc_nang.insertOne({
  _id: ObjectId(),
  ma_chuc_nang: "SALARY_WRITE",
  ten_chuc_nang: "Write Salary",
  vai_tro: [
    { value: "HR_MANAGER", label: "HR Manager" }
  ],
  don_vi_cong_tac: [],
  phong_ban_phu_trach: [],
  danh_sach_nguoi_dung: [],
  instance_name: "HR_System",
  is_active: true,
  is_deleted: false,
  ngay_tao: new Date()
});

// Permission 3: Document View
db.instance_data_danh_sach_phan_quyen_chuc_nang.insertOne({
  _id: ObjectId(),
  ma_chuc_nang: "DOCUMENT_VIEW",
  ten_chuc_nang: "View Documents",
  vai_tro: [
    { value: "HR_MANAGER", label: "HR Manager" }
  ],
  don_vi_cong_tac: [],
  phong_ban_phu_trach: [],
  danh_sach_nguoi_dung: [],
  instance_name: "Document_System",
  is_active: true,
  is_deleted: false,
  ngay_tao: new Date()
});

// Verify all permissions for the role
db.instance_data_danh_sach_phan_quyen_chuc_nang.find({
  vai_tro: { $elemMatch: { value: "HR_MANAGER" } }
}).toArray();
```

---

## Step 4: Setup Instance Mappings

```javascript
// Map functions to instances (sys_conf_view)

// SALARY_READ → HR_System
db.instance_data_sys_conf_view.insertOne({
  _id: ObjectId(),
  ma_chuc_nang: "SALARY_READ",
  ten_chuc_nang: "Read Salary",
  instance_name: "HR_System",
  table_name: "nhan_vien",
  is_active: true,
  is_deleted: false
});

// SALARY_WRITE → HR_System
db.instance_data_sys_conf_view.insertOne({
  _id: ObjectId(),
  ma_chuc_nang: "SALARY_WRITE",
  ten_chuc_nang: "Write Salary",
  instance_name: "HR_System",
  table_name: "nhan_vien",
  is_active: true,
  is_deleted: false
});

// DOCUMENT_VIEW → Document_System
db.instance_data_sys_conf_view.insertOne({
  _id: ObjectId(),
  ma_chuc_nang: "DOCUMENT_VIEW",
  ten_chuc_nang: "View Documents",
  instance_name: "Document_System",
  table_name: "documents",
  is_active: true,
  is_deleted: false
});

// Verify all mappings
db.instance_data_sys_conf_view.find({
  ma_chuc_nang: { $in: ["SALARY_READ", "SALARY_WRITE", "DOCUMENT_VIEW"] }
}).toArray();
```

---

## 🧪 Test Queries

### Check API Key
```javascript
db.instance_data_danh_sach_api_key.findOne({
  api_key: "818eccbf414d45918ec7e196de10737d"
});
// Should return document with is_active: true, is_deleted: false
```

### Check User
```javascript
db.instance_data_thong_tin_nhan_vien.findOne({
  ten_dang_nhap: "john_doe"
});
// Should return user with vai_tro and other fields
```

### Check User Permissions
```javascript
db.instance_data_danh_sach_phan_quyen_chuc_nang.find({
  vai_tro: { $elemMatch: { value: "HR_MANAGER" } }
}).toArray();
// Should return 3 functions: SALARY_READ, SALARY_WRITE, DOCUMENT_VIEW
```

### Check Instance Mappings
```javascript
db.instance_data_sys_conf_view.find({
  ma_chuc_nang: { $in: ["SALARY_READ", "SALARY_WRITE", "DOCUMENT_VIEW"] }
}).toArray();
// Should return all 3 functions with instance_name
```

---

## 🔍 Full Verification Query

```javascript
// Run this to verify complete setup
var api_key = "818eccbf414d45918ec7e196de10737d";
var username = "john_doe";

console.log("=== Verification ===");

// 1. Check API Key
var apiKeyDoc = db.instance_data_danh_sach_api_key.findOne({
  api_key: api_key
});
console.log("1. API Key exists:", !!apiKeyDoc);
if (apiKeyDoc) {
  console.log("   - is_active:", apiKeyDoc.is_active);
  console.log("   - is_deleted:", apiKeyDoc.is_deleted);
  console.log("   - username:", apiKeyDoc.ten_dang_nhap);
}

// 2. Check User
var userDoc = db.instance_data_thong_tin_nhan_vien.findOne({
  ten_dang_nhap: username
});
console.log("2. User exists:", !!userDoc);
if (userDoc) {
  console.log("   - roles:", userDoc.vai_tro.map(r => r.value));
  console.log("   - department:", userDoc.don_vi_cong_tac?.value);
}

// 3. Check Permissions
var roles = userDoc?.vai_tro?.map(r => r.value) || [];
var perms = db.instance_data_danh_sach_phan_quyen_chuc_nang.find({
  vai_tro: { $elemMatch: { value: { $in: roles } } }
}).toArray();
console.log("3. Permissions count:", perms.length);
perms.forEach(p => {
  console.log("   -", p.ma_chuc_nang, "->", p.instance_name);
});

// 4. Check Instance Mappings
var mas = perms.map(p => p.ma_chuc_nang);
var mappings = db.instance_data_sys_conf_view.find({
  ma_chuc_nang: { $in: mas }
}).toArray();
console.log("4. Instance mappings count:", mappings.length);
mappings.forEach(m => {
  console.log("   -", m.ma_chuc_nang, "->", m.instance_name);
});

console.log("\n=== Summary ===");
console.log("✓ API key ready" if (apiKeyDoc && apiKeyDoc.is_active && !apiKeyDoc.is_deleted));
console.log("✓ User exists" if userDoc);
console.log("✓ Permissions configured: " + perms.length);
console.log("✓ Instance mappings ready: " + mappings.length);
```

---

## 🚀 Test the Integration

Once setup is complete, test with:

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the salary for employee Ma01?",
    "session_id": "test-123"
  }'
```

**Expected Response** (✅ 200 OK):
```json
{
  "message": "The salary for employee Ma01 is...",
  "session_id": "test-123",
  "user_id": "...",
  "data": {...}
}
```

---

## 📋 Checklist

- [ ] API key inserted with is_active=true
- [ ] User exists in nhan_vien collection
- [ ] User has vai_tro assigned
- [ ] Permissions created for each vai_tro
- [ ] Instance mappings created for all ma_chuc_nang
- [ ] Test query shows all 4 steps verified
- [ ] curl test returns 200 OK
- [ ] User context in logs shows correct permissions

---

## 🔧 Quick Cleanup (if needed)

```javascript
// Remove test data
db.instance_data_danh_sach_api_key.deleteOne({
  api_key: "818eccbf414d45918ec7e196de10737d"
});

db.instance_data_thong_tin_nhan_vien.deleteOne({
  ten_dang_nhap: "john_doe"
});

db.instance_data_danh_sach_phan_quyen_chuc_nang.deleteMany({
  vai_tro: { $elemMatch: { value: "HR_MANAGER" } }
});

db.instance_data_sys_conf_view.deleteMany({
  ma_chuc_nang: { $in: ["SALARY_READ", "SALARY_WRITE", "DOCUMENT_VIEW"] }
});
```

---

## 💡 Tips

1. **Always use the same username** between API key and nhan_vien
2. **Make sure roles match** between nhan_vien and phan_quyen_chuc_nang
3. **Verify is_active and is_deleted** - very common cause of 401 errors
4. **Check ngay_het_han_token** if you set expiry
5. **Use MongoDB Compass** to visualize the data
6. **Enable debug logging** to see what's happening:
   ```python
   logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
   ```
