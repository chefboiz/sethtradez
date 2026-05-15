module.exports = {
  apps: [{
    name: "sethtradez",
    script: "/root/sethtradez/venv/bin/python",
    args: "main.py",
    cwd: "/root/sethtradez",
    interpreter: "none",
    watch: false,
    restart_delay: 10000,
    max_restarts: 20,
    min_uptime: "10s",
    env: {
      NODE_ENV: "production"
    },
    log_date_format: "YYYY-MM-DD HH:mm:ss",
    error_file: "/root/sethtradez/logs/sethtradez-error.log",
    out_file: "/root/sethtradez/logs/sethtradez-out.log",
    merge_logs: true
  }]
};
