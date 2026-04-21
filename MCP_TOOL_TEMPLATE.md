# MCP Tool Template: Permission-Aware Tool Implementation

## Overview

This document shows how MCP tools should retrieve and validate user permissions from the session context before returning results.

## Pattern: Extract Context → Validate → Query → Filter

```
MCP Tool receives augmented query:
  "[session_id:uuid-123] [username:john.doe] [don_vi:HR-01] [company:ABC]
   Tháng 4 có bao nhiêu đơn đi muộn về sớm?"

1. Extract session_id from query prefix
   └─ session_id = "uuid-123"

2. Retrieve user context from session store
   └─ context = session_store.get_context("uuid-123")
   └─ Returns: {user_id, username, company_code, don_vi_code, accessible_context}

3. Validate user has permission for this tool
   └─ if not context.permissions.get("tool_name"): raise PermissionError

4. Query database with user filters
   └─ WHERE company_code = context.company_code
   └─ AND don_vi_code = context.don_vi_code (if applicable)

5. Return filtered results (user already has access)
```

## Example Implementation

### MCP Tool: `hrm_req_list_requests`

**Purpose**: List all leave request types (late arrival, early departure, leave, etc.) for a given month

**Input**:
```python
{
  "session_id": "uuid-123",      # Extracted from query prefix
  "month": 4,
  "year": 2024
}
```

**Processing**:

```python
from workflow.session import session_store
import logging

logger = logging.getLogger(__name__)

def hrm_req_list_requests(
    session_id: str,
    month: int,
    year: int,
) -> dict:
    """
    List leave requests for a specific month.

    Permission-aware: Returns only data user is authorized to see.

    Args:
        session_id: From query prefix [session_id:xxx]
        month: Month number (1-12)
        year: Year

    Returns:
        {
            "requests": [
                {
                    "id": "req-001",
                    "employee_id": "emp-123",
                    "employee_name": "Nguyễn Văn A",
                    "type": "đi muộn",
                    "request_date": "2024-04-01",
                    "status": "Chấp thuận"
                },
                ...
            ],
            "total": 3,
            "user_company": "ABC",
            "user_don_vi": "HR-01"
        }
    """
    try:
        # 1. Retrieve user context from session
        context = session_store.get_context(session_id)
        if not context:
            logger.error("Session not found: %s", session_id)
            return {"error": "Session expired", "requests": []}

        user_id = context.get("user_id")
        username = context.get("username")
        company_code = context.get("company_code")
        don_vi_code = context.get("don_vi_code")
        accessible_context = context.get("accessible_context", {})

        logger.info(
            "hrm_req_list_requests: user=%s company=%s don_vi=%s month=%d/%d",
            username, company_code, don_vi_code, month, year,
        )

        # 2. Validate user has permission for this tool
        # Check if user has access to "leave_requests" collection
        if "leave_requests" not in accessible_context:
            logger.warning(
                "User %s denied access to leave_requests",
                username,
            )
            return {
                "error": f"User {username} is not authorized to view leave requests",
                "requests": []
            }

        # Get accessible instances for this collection
        accessible_instances = accessible_context.get("leave_requests", [])
        if not accessible_instances:
            logger.warning(
                "User %s has no accessible instances for leave_requests",
                username,
            )
            return {
                "error": "No accessible data instances",
                "requests": []
            }

        # 3. Query database with user filters
        # Note: This is pseudocode - actual implementation depends on your database
        from app.db.mongo import mongodb  # or your database connection

        requests = list(mongodb["leave_requests"].find({
            "company_code": company_code,
            # Don't filter by don_vi_code if user is admin/HR manager
            # This is an example - adapt to your permission model
            **({"don_vi_code": don_vi_code} if "admin" not in context.get("roles", []) else {}),
            # Filter by month/year
            "request_date": {
                "$gte": f"{year}-{month:02d}-01",
                "$lt": f"{year}-{month:02d}-32",
            }
        }))

        logger.debug(
            "Found %d requests for user %s in %d/%d",
            len(requests), username, month, year,
        )

        # 4. Return filtered results
        return {
            "requests": requests,
            "total": len(requests),
            "user_company": company_code,
            "user_don_vi": don_vi_code,
            "user_accessible_instances": accessible_instances,
        }

    except Exception as e:
        logger.error("hrm_req_list_requests error: %s", e, exc_info=True)
        return {"error": str(e), "requests": []}
```

### MCP Tool: `hrm_emp_view_profile`

**Purpose**: Get employee profile information

**Input**:
```python
{
  "session_id": "uuid-123",
  "employee_id": "emp-123"  # Can be "self" to get current user's profile
}
```

**Processing**:

```python
def hrm_emp_view_profile(
    session_id: str,
    employee_id: str = "self",
) -> dict:
    """
    Get employee profile information.

    Permission-aware: Only return employee data if user has permission.

    Args:
        session_id: From query prefix
        employee_id: Employee ID or "self" for current user

    Returns:
        {
            "employee_id": "emp-123",
            "name": "Nguyễn Văn A",
            "email": "nguyena@company.com",
            "position": "HR Manager",
            "don_vi": "HR-01",
            "start_date": "2020-01-15",
            "salary": 15000000  # Only if user has salary view permission
        }
    """
    try:
        # 1. Retrieve user context
        context = session_store.get_context(session_id)
        if not context:
            return {"error": "Session expired"}

        user_id = context.get("user_id")
        username = context.get("username")
        company_code = context.get("company_code")
        roles = context.get("roles", [])
        accessible_instances = context.get("accessible_context", {})

        # Handle "self" reference
        if employee_id == "self":
            employee_id = user_id

        logger.info(
            "hrm_emp_view_profile: requester=%s target_employee=%s",
            username, employee_id,
        )

        # 2. Validate permission
        # User can always view their own profile
        if employee_id != user_id:
            # For other employees, need "emp_view_all" or manager permission
            if "emp_view_all" not in accessible_instances.get("employee_profiles", []):
                logger.warning(
                    "User %s denied access to employee %s profile",
                    username, employee_id,
                )
                return {
                    "error": f"Permission denied to view profile for {employee_id}"
                }

        # 3. Query database with filters
        from app.db.mongo import mongodb

        employee = mongodb["employee_profiles"].find_one({
            "employee_id": employee_id,
            "company_code": company_code,
        })

        if not employee:
            logger.warning(
                "Employee not found: %s (company=%s)",
                employee_id, company_code,
            )
            return {"error": "Employee not found"}

        # 4. Filter response based on permissions
        result = {
            "employee_id": employee["employee_id"],
            "name": employee["name"],
            "email": employee["email"],
            "position": employee["position"],
            "don_vi": employee["don_vi"],
            "start_date": employee["start_date"],
        }

        # Only include salary if user has permission
        can_view_salary = (
            "admin" in roles or
            "salary_view" in accessible_instances.get("employee_salaries", [])
        )
        if can_view_salary:
            result["salary"] = employee.get("salary")
        else:
            logger.debug(
                "Salary hidden for user %s viewing employee %s",
                username, employee_id,
            )

        # Only include performance data if user has permission
        can_view_performance = (
            "admin" in roles or
            "performance_view" in accessible_instances.get("performance_reviews", [])
        )
        if can_view_performance:
            result["performance_score"] = employee.get("performance_score")

        logger.info(
            "Returned profile for %s (fields: %d)",
            employee_id, len(result),
        )
        return result

    except Exception as e:
        logger.error("hrm_emp_view_profile error: %s", e, exc_info=True)
        return {"error": str(e)}
```

## Parsing Context from Query

When an MCP tool receives a query from the agent, the context is embedded in the query prefix:

```
Query: "[session_id:uuid-123] [username:john.doe] [don_vi:HR-01] [company:ABC]
Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
```

### Extract Context Helper Function

```python
import re

def extract_context_from_query(query: str) -> dict:
    """
    Extract session_id, username, don_vi, company from query prefix.

    Returns:
        {
            "session_id": "uuid-123",
            "username": "john.doe",
            "don_vi": "HR-01",
            "company": "ABC",
            "actual_query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
        }
    """
    context = {}

    # Extract [key:value] pairs from query start
    pattern = r'\[(\w+):([^\]]+)\]'
    matches = re.findall(pattern, query[:200])  # Search only first 200 chars

    for key, value in matches:
        context[key] = value

    # Remove context prefix from actual query
    actual_query = re.sub(pattern, '', query).strip()

    context["actual_query"] = actual_query

    return context

# Example usage:
context = extract_context_from_query(query)
session_id = context["session_id"]
username = context["username"]
actual_query = context["actual_query"]
```

## Security Best Practices

### 1. Always Retrieve Full Context

```python
# ✅ CORRECT: Get full context
context = session_store.get_context(session_id)
if not context:
    raise PermissionError("Invalid session")

# ❌ WRONG: Don't trust the embedded context string
username = query.split("[username:")[1].split("]")[0]  # Unsafe!
```

### 2. Validate Permissions for Every Operation

```python
# ✅ CORRECT: Check permission before each operation
if "leave_requests" not in context.get("accessible_context", {}):
    raise PermissionError("User cannot access leave requests")

# ❌ WRONG: Trust agent to validate
# (Agent doesn't validate - it just calls tools)
```

### 3. Apply Row-Level Security Filters

```python
# ✅ CORRECT: Filter query results by user context
results = db.query(
    "SELECT * FROM requests WHERE company_code = ?",
    (context["company_code"],)
)

# ❌ WRONG: Return all results
results = db.query("SELECT * FROM requests")
```

### 4. Log All Access for Audit

```python
# ✅ CORRECT: Log permission checks
logger.info(
    "Tool %s: user=%s action=access resource=%s result=success",
    tool_name, username, resource_name,
)

logger.warning(
    "Tool %s: user=%s action=access resource=%s result=denied reason=%s",
    tool_name, username, resource_name, deny_reason,
)

# ❌ WRONG: Silent failures
# (No one will know when permissions are denied)
```

### 5. Never Expose Sensitive Data

```python
# ✅ CORRECT: Omit sensitive fields when no permission
result = {
    "name": employee["name"],
    "email": employee["email"],
    # salary omitted if user lacks permission
}

# ❌ WRONG: Return everything and hope frontend hides it
result = employee  # Includes salary, bank info, etc.
```

## Testing Permission-Aware Tools

### Unit Test Example

```python
import pytest
from workflow.session import session_store

@pytest.fixture
def test_session():
    """Create test session with known context."""
    session_id = "test-session-123"
    session_store.save_context(
        session_id=session_id,
        user_id="test-user-1",
        username="testuser",
        accessible={
            "leave_requests": ["sys_admin", "instance_hrm_01"],
            "employee_profiles": ["instance_hrm_01"],
        },
        company_code="TEST",
    )
    return session_id

def test_hrm_req_list_requests_with_permission(test_session):
    """Test that tool returns results when user has permission."""
    result = hrm_req_list_requests(
        session_id=test_session,
        month=4,
        year=2024,
    )
    assert "error" not in result
    assert isinstance(result["requests"], list)

def test_hrm_req_list_requests_without_permission():
    """Test that tool denies access when user lacks permission."""
    # Create session with NO leave_requests access
    session_id = "test-session-no-perm"
    session_store.save_context(
        session_id=session_id,
        user_id="test-user-2",
        username="restricted",
        accessible={
            "employee_profiles": ["instance_hrm_01"],
            # No leave_requests permission
        },
        company_code="TEST",
    )

    result = hrm_req_list_requests(
        session_id=session_id,
        month=4,
        year=2024,
    )
    assert "error" in result
    assert result["requests"] == []

def test_hrm_emp_view_profile_self():
    """Test that users can view their own profile."""
    session_id = "test-session-self"
    session_store.save_context(
        session_id=session_id,
        user_id="user-123",
        username="john.doe",
        accessible={},
        company_code="TEST",
    )

    result = hrm_emp_view_profile(
        session_id=session_id,
        employee_id="self",
    )
    # Should succeed - user viewing self
    assert "error" not in result or "error" in result  # Depends on DB

def test_hrm_emp_view_profile_other_denied():
    """Test that users can't view other profiles without permission."""
    session_id = "test-session-other"
    session_store.save_context(
        session_id=session_id,
        user_id="user-123",
        username="john.doe",
        accessible={
            "employee_profiles": ["instance_hrm_01"],
            # No emp_view_all permission
        },
        company_code="TEST",
    )

    result = hrm_emp_view_profile(
        session_id=session_id,
        employee_id="other-user-456",  # Different employee
    )
    assert "error" in result
    assert "denied" in result["error"].lower()
```

## Integration with Agent

When an agent calls a tool, it passes the session_id and augmented query:

```python
# Agent code (in AgentOS Team)
async def process_user_request(user_query: str, session_id: str):
    """
    Agent processes request and calls tools with context.
    """
    # Agent receives: "[session_id:uuid] [username:john] ... actual query"
    
    # Agent extracts session_id
    session_id = extract_session_id_from_query(user_query)
    
    # Agent calls MCP tools
    result = await call_mcp_tool(
        tool_name="hrm_req_list_requests",
        session_id=session_id,
        month=4,
        year=2024,
    )
    
    # Tool internally:
    # 1. Calls session_store.get_context(session_id)
    # 2. Validates permissions
    # 3. Queries database
    # 4. Returns filtered results
    
    return result
```

## Related Files

- `workflow/session.py` - SessionStore implementation with `get_context()`
- `utils/permission.py` - UserPermissionContext definition
- `app/db/mongo.py` - Database connection (adapt for your DB)
- Test files for permission validation

