# 🔗 Kết nối AgentOS với Control Plane

## Mô tả
Hướng dẫn kết nối HITC AgentOS với **Agno Control Plane** (os.agno.com) để:
- ✅ Monitor agents in real-time
- ✅ Manage sessions và conversations
- ✅ View metrics, logs, performance
- ✅ Test agents từ dashboard web

---

## 📋 Yêu cầu

1. **AgentOS running** trên:
   - `http://localhost:8000` (local development)
   - `https://yourdomain.com` (production)

2. **Agno Account** - Đăng ký tại https://os.agno.com/

3. **.env configuration**:
   ```env
   # AgentOS Control Plane settings
   AGENTOSAGNO_NAME=HITC AgentOS
   AGENTOSAGNO_DESCRIPTION=Multi-Agent System for HITC workflows
   AGENTOSAGNO_ENDPOINT=http://localhost:8000  # Local dev
   # AGENTOSAGNO_ENDPOINT=https://agents.hitc.vn  # Production
   AGENTOSAGNO_API_KEY=your_control_plane_api_key  # If required
   ```

---

## 🚀 Cách kết nối (Step-by-step)

### Bước 1: Ensure AgentOS is Running

```bash
# Terminal 1: Start your AgentOS
cd F:\HITC\modatav2\modata.agent.system
python run.py
# Server sẽ start tại http://localhost:8000
```

Verify AgentOS is up:
```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Bước 2: Verify AgentOS Endpoints

Kiểm tra các endpoints mà AgentOS cung cấp:
```bash
# Check available agents
curl http://localhost:8000/agents/

# Example agents endpoints:
# POST /agents/checkin-agent/runs
# POST /agents/data-query-agent/runs
# POST /agents/analytics-agent/runs
# POST /agents/email-agent/runs
# POST /agents/search-docs-agent/runs
```

### Bước 3: Đăng nhập os.agno.com

1. Mở https://os.agno.com/
2. Sign in hoặc Create account
3. Click **"Add new OS"** button

### Bước 4: Configure Connection

**Form fields**:

| Field | Value | Example |
|-------|-------|---------|
| **Environment** | Local hoặc Live | Local (for development) |
| **Endpoint URL** | Nơi AgentOS running | http://localhost:8000 |
| **OS Name** | Descriptive name | HITC AgentOS |
| **Tags** | Organize label (optional) | dev, testing |

**Example configuration**:
```
Environment: Local
Endpoint URL: http://localhost:8000
OS Name: HITC AgentOS
Tags: dev, hitc, testing
```

### Bước 5: Click "CONNECT"

Control plane sẽ:
- ✅ Verify kết nối đến AgentOS
- ✅ Fetch danh sách agents
- ✅ Test endpoints

### Bước 6: Verify Connection Status

Trong Control Plane dashboard, bạn sẽ thấy:

```
✓ Status: Running
✓ Features: Chat, Knowledge, Memory, Sessions
✓ Agents configured:
  - Check-in Agent
  - Data Query Agent
  - Analytics Agent
  - Email Agent
  - Search Docs Agent
```

---

## 🔍 Verify AgentOS is Connected

### Trong Control Plane:

1. **Chat Interface**:
   - Select agent từ dropdown
   - Type message
   - Agent responds thông qua AgentOS

2. **Monitor Tab**:
   - View agent sessions
   - Check latency, success rate
   - View logs

3. **Agents Tab**:
   - Xem chi tiết mỗi agent
   - View description, system prompt
   - Test individual agents

### Trong Local:

Check logs cho "Control Plane connected" message:
```
✓ AgentOS initialized: HITC AgentOS (5 agents)
✓ To connect to Control Plane, go to os.agno.com and add: http://localhost:8000
```

---

## 🌐 Chuyển từ Local sang Production

Khi ready deploy lên production:

### 1. Setup HTTPS

**Requirement**: Control plane requires HTTPS for production

Options:
- **Let's Encrypt** (free): Use certbot
- **Self-signed** (testing): Accept in control plane
- **AWS Certificate Manager** (AWS): Auto-managed

### 2. Update .env

```env
# Production configuration
AGENTOSAGNO_ENDPOINT=https://agents.yourdomain.com
AGENTOSAGNO_API_KEY=your_production_api_key

LLM_BASE_URL=https://llm.yourdomain.com:8088
MCP_GATEWAY_URL=https://mcp.yourdomain.com/sse
```

### 3. Update in Control Plane

1. Go to os.agno.com → Your OS
2. Click **"Edit"**
3. Change:
   - Environment: **Live**
   - Endpoint URL: **https://agents.yourdomain.com**
4. Click **"Save"**

### 4. Verify Production Connection

```bash
# Test from Control Plane dashboard
# Should see: Status: Running ✓
```

---

## 🛠️ Troubleshooting

### ❌ Connection Refused

**Problem**: `Connection refused` saat connect

**Solution**:
```bash
# 1. Verify AgentOS is running
curl http://localhost:8000/health

# 2. Check firewall
# Port 8000 harus buka

# 3. Check .env
echo $AGENTOSAGNO_ENDPOINT
# Should match localhost:8000 (dev) or https domain (prod)
```

### ❌ "Unable to fetch agents"

**Problem**: Control plane cannot reach agents list

**Solution**:
```bash
# Test endpoint manually
curl http://localhost:8000/agents/

# Should return JSON list of agents
# If not, AgentOS not fully initialized
```

### ❌ "Agent timeout"

**Problem**: Agents respond slowly or timeout

**Solution**:
```bash
# Check MCP Gateway connection
curl http://localhost:8001/sse
# Should connect

# Check LLM server
curl http://192.168.100.114:8088/v1/models
# Should return model list

# Increase timeout in config
LLM_TIMEOUT=120  # 2 minutes
```

### ❌ "Authentication failed"

**Problem**: Khi connect production

**Solution**:
```bash
# Verify HTTPS certificate valid
openssl s_client -connect agents.yourdomain.com:443

# Check API key (if required)
AGENTOSAGNO_API_KEY=your_valid_key
```

---

## 📊 Control Plane Features

### Chat Interface

- **Test individual agents** before deployment
- **Multi-turn conversations** with context
- **View agent responses** in real-time
- **Analyze performance** metrics

### Monitor Tab

- **Active sessions** count
- **Request/response** times
- **Error rates** & logs
- **Agent availability** status

### Knowledge Tab

- **Manage RAG documents**
- **Upload knowledge bases**
- **View indexed content**
- **Search performance**

### Settings Tab

- **Update endpoint URL**
- **Manage API keys**
- **Configure tags**
- **Team members & permissions**

---

## 📝 Logging & Debugging

Enable debug logging để track connection:

```python
# app/core/config.py
LOG_LEVEL: str = "DEBUG"  # For troubleshooting
```

Check logs:
```bash
# Follow logs in real-time
tail -f logs/modata-agent.log | grep "AgentOS"

# Or in terminal output during run.py
```

Expected logs khi connect successfully:
```
[INFO] AgentOS initialized: HITC AgentOS (5 agents)
[INFO] To connect to Control Plane, go to os.agno.com and add: http://localhost:8000
[INFO] Control Plane connected successfully
```

---

## 🎯 Next Steps

After connecting successfully:

1. **Test each agent** via Control Plane dashboard
2. **Monitor metrics** để identify bottlenecks
3. **Collect feedback** từ users
4. **Optimize prompts** based on agent performance
5. **Scale to production** khi ready

---

## 📚 Resources

- **Agno Docs**: https://docs.agno.com/
- **AgentOS Docs**: https://docs.agno.com/agent-os/
- **Control Plane**: https://os.agno.com/
- **GitHub**: https://github.com/agno-agi/agno

---

**Version**: 1.0
**Last Updated**: 2026-04-03
**Status**: ✅ Ready for Production
