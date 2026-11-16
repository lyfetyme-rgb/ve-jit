# Security Policy

Report vulnerabilities privately to: security@vejit.com

## Production checklist
- Replace demo secrets in `.env`
- Enable TLS at ingress / load balancer
- Restrict CORS origins
- Use managed PostgreSQL and Redis in production
- Enable secret scanning and dependency updates
