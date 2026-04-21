# Exact Changes Required: AgentOS Fix

This document shows the exact code changes needed to deploy the fix.

---

## File 1: NEW FILE - `workflow/agentosagno_hooks.py`

**Status**: Create new file (full file content provided)

**Location**: `f:\HITC\modatav2\modata.agent.system\workflow\agentosagno_hooks.py`

**Size**: ~191 lines

**Content**: See the actual file created. Contains:
- `_reconstruct_user_context_from_session()` function
- `get_context_injecting_agent_os()` function with monkey-patching

---

## File 2: MODIFIED FILE - `workflow/hitc_agent.py`

**Status**: Update existing file with 2 changes

**Location**: `f:\HITC\modatav2\modata.agent.system\workflow\hitc_agent.py`

### Change 1: Add Import (Around Line 50)

**Before**:
```python
from utils.qwen_model import QwenOpenAILike as OpenAILike
from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

# ── Import teams ─────────────────────────────────────────────
from workflow.hrm_team import (
```

**After**:
```python
from utils.qwen_model import QwenOpenAILike as OpenAILike
from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store
from workflow.agentosagno_hooks import get_context_injecting_agent_os  # ← ADD THIS LINE

# ── Import teams ─────────────────────────────────────────────
from workflow.hrm_team import (
```

### Change 2: Apply Wrapper in `_get_hitc_agent_os()` (Around Line 193)

**Before**:
```python
        _agent_os = AgentOS(
            name="HITC AgentOS",
            description=(
                "Hệ thống AI đa tác nhân HITC — điều phối HRM Team và "
                "Document Intelligence Team để xử lý mọi yêu cầu nội bộ."
            ),
            teams=[hrm_team, doc_team],
            db=db,
            registry=registry,
        )

        logger.info(
            "✓ HITC AgentOS initialized: %d teams (HRM + Document)",
            2,
        )
```

**After**:
```python
        _agent_os = AgentOS(
            name="HITC AgentOS",
            description=(
                "Hệ thống AI đa tác nhân HITC — điều phối HRM Team và "
                "Document Intelligence Team để xử lý mọi yêu cầu nội bộ."
            ),
            teams=[hrm_team, doc_team],
            db=db,
            registry=registry,
        )
        
        # ✨ Wrap AgentOS to inject session context into agents
        #    This fixes "Function not found" errors for MCP tools
        _agent_os = get_context_injecting_agent_os(_agent_os)  # ← ADD THIS LINE

        logger.info(
            "✓ HITC AgentOS initialized: %d teams (HRM + Document)",
            2,
        )
```

---

## Summary of Changes

| File | Type | Lines Changed | Impact |
|------|------|---------------|--------|
| `workflow/agentosagno_hooks.py` | NEW | 191 total | Context injection logic |
| `workflow/hitc_agent.py` | MODIFIED | 2 additions | Apply wrapper to AgentOS |

**Total lines of code added**: ~193 lines
**Breaking changes**: None
**Backwards compatibility**: Fully compatible

---

## Verification Commands

After making changes, verify:

```bash
# Check syntax
python -m py_compile workflow/agentosagno_hooks.py
python -m py_compile workflow/hitc_agent.py

# Check imports work
python -c "from workflow.agentosagno_hooks import get_context_injecting_agent_os; print('✓ Import successful')"

# Check hitc_agent imports everything
python -c "from workflow.hitc_agent import create_hitc_agent_os_app; print('✓ hitc_agent imports ok')"
```

---

## Testing After Changes

```bash
# Test 1: Direct call (should work before and after)
curl -X POST "http://localhost:8000/hitc/chat" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Get employee data",
    "session_id": "test-verify-001"
  }'

# Test 2: AgentOS endpoint (should work after fix)
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data",
    "session_id": "test-verify-002"
  }'

# Both should return 200 OK with data, NOT "Function not found" errors
```

---

## Deployment Steps

1. **Copy new file**:
   ```bash
   cp workflow/agentosagno_hooks.py <destination>/workflow/agentosagno_hooks.py
   ```

2. **Update existing file**:
   - Add import on line ~50
   - Add wrapper application on line ~196
   - (See exact changes above)

3. **Restart application**:
   ```bash
   # Stop current app
   # Run app again
   python run.py
   ```

4. **Verify**:
   ```bash
   # Check logs for:
   # "[ContextInjection] ✨ AgentOS wrapped with context injection enabled"
   grep "[ContextInjection]" logs/app.log
   ```

---

## Rollback (If Needed)

To rollback in emergency:

**Option 1 - Disable without removing file**:
```python
# In workflow/hitc_agent.py, comment out line ~196:
# _agent_os = get_context_injecting_agent_os(_agent_os)
```

**Option 2 - Full rollback**:
```bash
# Remove new file
rm workflow/agentosagno_hooks.py

# Revert hitc_agent.py to previous version
git checkout workflow/hitc_agent.py

# Restart app
```

---

## Files Created/Modified

### ✅ Created: `workflow/agentosagno_hooks.py`
- Full context injection logic
- Ready to deploy
- No additional dependencies

### ✅ Modified: `workflow/hitc_agent.py`
- 1 import line added
- 1 function call added
- Fully compatible with existing code

### ✓ No changes: `workflow/session.py`
- Already has `get_context()` method
- No modifications needed

### ✓ No changes: Other files
- No breaking changes
- Backwards compatible

---

## Expected Outcome

After deploying these changes:

```
Before:
POST /teams/hrm-team/runs → ERROR: Function not found ❌

After:
POST /teams/hrm-team/runs → Success: Employee data returned ✅
```

Logs will show:
```
[ContextInjection] ✨ AgentOS wrapped with context injection enabled
[ContextInjection] Injecting context: team=hrm-team, session=test-verify-002, user=...
[ContextInjection] ✓ Context injected successfully
```

---

## Questions Before Deployment?

Review these documents:
1. `FIX_SUMMARY_AGENTOSAGNO.md` - Overview of the fix
2. `SOLUTION_AGENTOSAGNO_FUNCTION_NOT_FOUND.md` - Technical details
3. `TESTING_AGENTOSAGNO_FIX.md` - How to test

---

## Deployment Checklist

- [ ] Read and understand `FIX_SUMMARY_AGENTOSAGNO.md`
- [ ] Copy `workflow/agentosagno_hooks.py` to project
- [ ] Update `workflow/hitc_agent.py` with 2 changes
- [ ] Run syntax verification
- [ ] Test both endpoints
- [ ] Check logs for "[ContextInjection]" messages
- [ ] Deploy to production
- [ ] Monitor for any issues

---

## Summary

This is a **minimal, focused fix** for the AgentOS "Function not found" error:

✅ 1 new file with context injection logic
✅ 2 lines added to existing file to enable wrapper
✅ No breaking changes
✅ Fully backwards compatible
✅ Ready for immediate deployment
