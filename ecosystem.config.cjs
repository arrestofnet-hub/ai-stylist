module.exports = {
  apps: [
    {
      name: "ai-stylist-api",
      cwd: "/opt/ai-stylist",
      script: "/opt/ai-stylist/.venv/bin/uvicorn",
      args: "app.main:app --host 127.0.0.1 --port 8010",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "ai-stylist-mcp",
      cwd: "/opt/ai-stylist",
      script: "/opt/ai-stylist/.venv/bin/python",
      args: "mcp_server.py",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
