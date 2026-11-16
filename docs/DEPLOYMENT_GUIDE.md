# Deployment Guide

## Local
```bash
docker compose up -d --build
```

## Validate
```bash
./scripts/verify-vejit-system.sh
```

## Production considerations
- Replace demo secrets
- Set managed database endpoints
- Configure ingress TLS
- Restrict CORS
- Add SSO and secret management
