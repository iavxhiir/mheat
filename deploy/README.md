# MHEAT VPS Deploy — one-page cheatsheet

Target: a cheap Linux VPS (Hetzner CX21 €5/mo, Contabo VPS-1 €6/mo, or DigitalOcean $6/mo droplet). Ubuntu 24.04 LTS recommended. 2 vCPU, 4 GB RAM, 40 GB disk is enough for the demo.

## 1. Provision the VPS
Pick any provider. During creation: **Ubuntu 24.04**, add your SSH public key, name the server `mheat-demo`.

## 2. Point a subdomain at the VPS
Buy or reuse a domain. Add an A record: `mheat.<your-domain>` → VPS public IP.

## 3. SSH in and bootstrap
Replace `<IP>` with your server IP, `<DOMAIN>` with your subdomain (e.g. `mheat.example.com`).

```bash
ssh root@<IP>

# system updates + docker + compose plugin
apt update && apt upgrade -y
apt install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# create app dir + clone
mkdir -p /opt/mheat && cd /opt/mheat
git clone https://github.com/<you>/mheat .    # or scp your local folder up
cp .env.example .env

# Optional: set live CMS credentials
# sed -i 's/^DEMO_MODE=.*/DEMO_MODE=false/' .env
# echo "COPERNICUSMARINE_SERVICE_USERNAME=<user>" >> .env
# echo "COPERNICUSMARINE_SERVICE_PASSWORD=<pass>" >> .env

docker compose up -d --build
```

Check: `curl http://localhost:8000/api/health` → should return `{"status":"ok"...}`.

## 4. TLS + public URL via Caddy (one file)

```bash
apt install -y caddy
cat >/etc/caddy/Caddyfile <<EOF
<DOMAIN> {
    reverse_proxy localhost:8000
    encode gzip
}
EOF
systemctl restart caddy
```

Caddy fetches a Let's Encrypt TLS certificate automatically. Browse `https://<DOMAIN>` — the MHEAT dashboard is now public.

## 5. Tips
- **Firewall**: Hetzner/Contabo enable UFW by default. Open 22, 80, 443: `ufw allow 22,80,443/tcp && ufw enable`.
- **Logs**: `docker compose logs -f mheat`.
- **Restart on boot**: Docker already adds `restart: unless-stopped` to the service.
- **Shutdown when done recording the demo**: `docker compose down` — VPS keeps running but no app.
- **Disk**: `docker system prune -f` frees old layers if the disk fills.

## 6. Minimum viable for the grant submission
The demo URL must be:
- **reachable from a browser anywhere** (proof of deployment),
- **up for at least 24 h** before you record the video,
- **ideally in DEMO_MODE** for deterministic visuals (57 events will always show). Switch to live CMS after submission.
