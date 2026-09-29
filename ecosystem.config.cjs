module.exports = {
  apps: [
    {
      name: "ai-stylist",
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
    }
  ]
};
