# Deploy Growth Autopsy on Amazon EC2 without Docker

This guide deploys the complete FastAPI backend, browser dashboard, background
scheduler, SQLite database, Chromium and Lighthouse on one Ubuntu EC2 instance.
Nginx terminates public HTTP/HTTPS traffic and proxies only to the application bound
on `127.0.0.1:8787`.

## 0. Publish the approved application revision

The EC2 server can clone only code that has been committed and pushed to GitHub.
Before creating the server, confirm that the approved local application revision is
on `main` and that secrets remain ignored:

```bash
git status --short
git diff --check
git check-ignore .env secrets/google-token.json
```

Both secret paths must be reported by `git check-ignore`. Do not copy `.env` or OAuth
tokens into Git; they are transferred directly in Section 8. Review and commit only
the approved application files, then publish them:

```bash
git commit -m "Prepare Growth Autopsy for EC2 production"
git push origin main
```

The commit command assumes the approved files have already been staged deliberately.

## 1. Confirm Free Tier eligibility and protect the account

AWS Free Tier eligibility depends on the account creation date:

- Accounts created on or after 15 July 2025 receive credits and a Free account plan
  for up to six months. EC2 types marked eligible include `t3.micro`, `t3.small`,
  `t4g.micro`, `t4g.small`, `c7i-flex.large` and `m7i-flex.large`.
- Older accounts use the legacy 12-month limits and generally only `t2.micro` or
  `t3.micro` qualify while that period remains active.

Confirm the current rules in the official
[EC2 Free Tier guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)
and the account's Billing → Free Tier page before choosing an instance.

Before launching anything:

1. Enable MFA on the AWS root account.
2. Open Billing → Budgets and create a low monthly cost budget/alert.
3. Open Billing → Free Tier and confirm remaining credits or legacy eligibility.
4. Use the Mumbai (`ap-south-1`) region unless the workload requires another region.

For a new credit-based account, choose `t3.small` when the EC2 console marks it
Free-tier eligible. Its 2 GB RAM is much safer for Chromium. Use `t3.micro` only when
necessary; the setup below adds swap and reduces concurrency for its 1 GB RAM.

## 2. Launch the instance

In EC2 → Instances → Launch instances, choose:

- Name: `growth-autopsy-production`
- AMI: Ubuntu Server 24.04 LTS, x86_64, marked Free-tier eligible
- Instance type: `t3.small` if eligible; otherwise `t3.micro`
- Key pair: create an RSA `.pem` key and download it once
- Storage: 20 GB `gp3`, encrypted
- Auto-assign public IPv4: enabled

Create a security group with only:

| Port | Source | Purpose |
|---|---|---|
| TCP 22 | My IP only | SSH administration |
| TCP 80 | `0.0.0.0/0`, `::/0` | HTTP and certificate setup |
| TCP 443 | `0.0.0.0/0`, `::/0` | Production HTTPS |

Never open port `8787` in the AWS security group.

For a stable DNS target, allocate one Elastic IP and associate it with the running
instance. AWS charges for public IPv4 addresses; qualifying credits/legacy allowances
may cover it. Review the official
[public IPv4 pricing notice](https://aws.amazon.com/blogs/aws/new-aws-public-ipv4-address-charge-public-ip-insights/)
and release an unused Elastic IP promptly.

## 3. Point a domain to the server

At the DNS provider, create an `A` record such as:

```text
growth.example.com → YOUR_ELASTIC_IP
```

Wait for it to resolve before requesting the TLS certificate:

```bash
dig +short growth.example.com
```

A controlled domain is required for a normal Let's Encrypt production certificate.
The raw EC2 public hostname is suitable only for the initial HTTP health test.

## 4. Connect from the Mac

```bash
chmod 400 ~/Downloads/growth-autopsy-ec2.pem
ssh -i ~/Downloads/growth-autopsy-ec2.pem ubuntu@YOUR_ELASTIC_IP
```

## 5. Patch Ubuntu and add swap

Run on EC2:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git curl ca-certificates nginx certbot python3-certbot-nginx sqlite3

sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab
free -h
```

Swap prevents abrupt out-of-memory termination during Chromium/Lighthouse work. It
does not make a micro instance fast.

## 6. Install Node 22 and uv

Lighthouse 13 requires Node 22.19 or newer:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh
sudo -E bash /tmp/nodesource_setup.sh
sudo apt install -y nodejs
node --version
npm --version

curl -LsSf https://astral.sh/uv/install.sh | sh
/home/ubuntu/.local/bin/uv --version
```

Do not continue unless Node is at least `v22.19.0`.

## 7. Clone and install the application

```bash
sudo install -d -o ubuntu -g ubuntu /opt/growth-autopsy
git clone https://github.com/reddyjai30/Growth-Autopsy.git /opt/growth-autopsy
cd /opt/growth-autopsy

/home/ubuntu/.local/bin/uv python install 3.11
/home/ubuntu/.local/bin/uv sync --frozen --extra dev
npm ci --omit=dev --no-audit --no-fund

PLAYWRIGHT_BROWSERS_PATH=/opt/growth-autopsy/.playwright-browsers \
  /home/ubuntu/.local/bin/uv run playwright install --with-deps chromium

PLAYWRIGHT_BROWSERS_PATH=/opt/growth-autopsy/.playwright-browsers \
  /home/ubuntu/.local/bin/uv run python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print(b.version); b.close(); p.stop()"

/home/ubuntu/.local/bin/uv run pytest
/home/ubuntu/.local/bin/uv sync --frozen
mkdir -p data secrets backups
chmod 700 data secrets backups
```

## 8. Transfer configuration and Google authorization

The safest starting point is to transfer the already-working local `.env` and
authorized Calendar token from the Mac. Open a second Mac terminal in the repository:

```bash
scp -i ~/Downloads/growth-autopsy-ec2.pem \
  .env ubuntu@YOUR_ELASTIC_IP:/tmp/growth-autopsy.env

scp -i ~/Downloads/growth-autopsy-ec2.pem \
  ./secrets/google-token.json ubuntu@YOUR_ELASTIC_IP:/tmp/google-token.json
```

Back in the EC2 SSH session:

```bash
sudo install -m 600 -o ubuntu -g ubuntu \
  /tmp/growth-autopsy.env /opt/growth-autopsy/.env
sudo install -m 600 -o ubuntu -g ubuntu \
  /tmp/google-token.json /opt/growth-autopsy/secrets/google-token.json
rm /tmp/growth-autopsy.env /tmp/google-token.json
nano /opt/growth-autopsy/.env
```

Change or confirm these production values:

```dotenv
GA_ENVIRONMENT=production
GA_DATABASE_PATH=/opt/growth-autopsy/data/growth_autopsy.db
GA_SHARED_WORKDIR=/opt/growth-autopsy/data

GA_APP_USERNAME=choose-a-private-username
GA_APP_PASSWORD=use-a-unique-password-with-at-least-12-characters
GA_SESSION_SECRET=paste-a-random-64-character-value
GA_SESSION_TTL_HOURS=12
GA_MANAGED_CONFIGURATION=false

GA_GOOGLE_TOKEN_FILE=/opt/growth-autopsy/secrets/google-token.json
GA_LIGHTHOUSE_EXECUTABLE=/opt/growth-autopsy/node_modules/.bin/lighthouse
GA_PRECALL_MAX_CONCURRENCY=2
GA_PRECALL_MAX_PARALLEL_APPOINTMENTS=1

GA_ENABLE_BACKGROUND_SYNC=true
GA_LINKEDIN_PUBLISH_AFTER_NOTION=false
```

Generate the session secret with:

```bash
openssl rand -hex 32
```

Keep all existing AI, Fathom and Notion credentials from the local `.env`. Never
commit `.env` or `secrets/`.

Initialize and verify:

```bash
cd /opt/growth-autopsy
/home/ubuntu/.local/bin/uv run growth-autopsy init-db
/home/ubuntu/.local/bin/uv run growth-autopsy calendar-check
```

## 9. Install the systemd service

```bash
sudo cp /opt/growth-autopsy/deploy/ec2/growth-autopsy.service \
  /etc/systemd/system/growth-autopsy.service
sudo systemctl daemon-reload
sudo systemctl enable --now growth-autopsy
sudo systemctl status growth-autopsy --no-pager
curl --fail --show-error http://127.0.0.1:8787/health
```

The app starts automatically after reboot and restarts after a process failure.

## 10. Configure Nginx

```bash
sudo cp /opt/growth-autopsy/deploy/ec2/nginx.conf \
  /etc/nginx/sites-available/growth-autopsy
sudo nano /etc/nginx/sites-available/growth-autopsy
```

Replace `YOUR_DOMAIN` with the real hostname, then:

```bash
sudo ln -s /etc/nginx/sites-available/growth-autopsy \
  /etc/nginx/sites-enabled/growth-autopsy
sudo unlink /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
curl --fail --show-error http://YOUR_DOMAIN/health
```

## 11. Enable HTTPS

```bash
sudo certbot --nginx -d YOUR_DOMAIN \
  --redirect --agree-tos --no-eff-email -m YOUR_EMAIL
sudo certbot renew --dry-run
curl --fail --show-error https://YOUR_DOMAIN/health
```

Opening `https://YOUR_DOMAIN` must show the Growth Autopsy sign-in page. Sign in and
verify Pipeline, Admin → Database and Admin → Configuration.

## 12. Move Fathom from ngrok to EC2

Replace the ngrok destination in Fathom with:

```text
https://YOUR_DOMAIN/webhooks/fathom
```

Keep transcript, summary and action items enabled. A valid signed webhook returns
HTTP `202`. Ngrok is no longer needed for the hosted instance.

## 13. Firewall, logs and recovery

Optionally enable Ubuntu's firewall after confirming SSH and Nginx rules:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Useful operating commands:

```bash
sudo journalctl -u growth-autopsy -f
sudo systemctl restart growth-autopsy
sudo systemctl status nginx --no-pager
df -h
free -h
```

Before an application update, create a consistent SQLite copy:

```bash
cd /opt/growth-autopsy
sqlite3 data/growth_autopsy.db \
  ".backup 'backups/growth_autopsy-before-update.db'"
```

Also schedule EBS snapshots or copy encrypted backups to a separate destination.
Keeping a backup only on the same EC2 root volume does not protect against volume
loss.

## 14. Deploy later updates

```bash
cd /opt/growth-autopsy
git pull --ff-only
/home/ubuntu/.local/bin/uv sync --frozen
npm ci --omit=dev --no-audit --no-fund
sudo systemctl restart growth-autopsy
sudo systemctl status growth-autopsy --no-pager
curl --fail --show-error https://YOUR_DOMAIN/health
```

Keep one application process and one EC2 instance while using SQLite and the
in-process scheduler.
